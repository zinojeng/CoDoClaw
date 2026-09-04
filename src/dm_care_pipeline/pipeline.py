"""
薄編排層：依序呼叫第1~9站、把物件往下傳。純膠水程式碼，不含任何判斷
邏輯（架構文件6節）。

因鐵律3（醫師決策只做決策支援，不做自動決策），管線無法一次「跑到底」：
`run_stages_1_to_7()` 跑到第7站產生一份全 PENDING 的
`PhysicianDecisionRecord` 就停下，交還呼叫端（醫師 UI）逐筆呼叫
`record_decision()`；等醫師決策完成後，呼叫端再呼叫
`finalize_pipeline()` 跑第8/9站。

★ v2 擴充（架構文件v2 3.14節）：`run_stages_1_to_7()`/`finalize_pipeline()`
既有簽名與行為完全不變（新參數皆有預設值，舊呼叫端零改動即可運作）。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Optional, Sequence

from dm_eligibility.models import EligibilityConfig, EligibilityReport, PatientEnrollmentState, PhysicianStatus

from .alert import AlertClassificationConfig
from .calculators import register_default_calculators
from .calculators.abi_tbi import ABITBIInputs
from .calculators.base import CalculatorExecutionStatus, CalculatorResult
from .calculators.bnp_hf_screen import NatriureticPeptideInputs
from .calculators.ckd_ga import CKDGAInputs
from .calculators.fib4 import FIB4Inputs
from .calculators.hypoglycemia_ada_l1 import HypoglycemiaRiskFactorInputs
from .calculators.iwgdf_foot import IWGDFFootInputs
from .calculators.registry import CalculatorRegistry, DEFAULT_CALCULATOR_REGISTRY
from .calculators.tier_b.karter_hypoglycemia import KarterHypoglycemiaInputs
from .calculators.tier_b.kfre import Kfre4VarInputs
from .calculators.tier_b.prevent_ascvd import LegacyAscvdPceInputs, PreventInputs
from .calculators.tier_b.watch_dm import WatchDmInputs
from .care_gap import CareGapReport, assess_care_gaps
from .care_gap_clocks import CareGapAgentConfig, CareGapAgentReport, assess_care_gap_agent
from .clinical_data_object import ClinicalStatus
from .clinical_state import ClinicalStateConfig, PatientClinicalState, derive_clinical_state
from .complication_identification import ComplicationConfig, ComplicationReport, identify_complications
from .data_integration import build_patient_clinical_profile
from .education import (
    EducationPlan,
    EducationReportBuilderConfig,
    EducationTopicMappingConfig,
    PatientEducationReport,
    generate_patient_education_report,
    select_education_topics,
)
from .followup import (
    ComplicationMonitoringConfig,
    FollowUpPlan,
    OrderTrackingConfig,
    OrderTrackingReport,
    PendingOrderSource,
    compute_follow_up_plan,
    track_pending_orders,
)
from .guideline_recommendation import (
    GuidelineRecommendationEngine,
    GuidelineRecommendationReport,
    RecommendationRule,
    build_guideline_input,
)
from .medication_intelligence import (
    MEDICATION_ATC_CLASS_MAP,
    MedicationIndicationRule,
    MedicationIntelligenceReport,
    build_medication_check_input,
    build_medication_intelligence_report,
)
from .physician_decision import DecisionValidationError, PhysicianDecisionRecord, present_for_decision
from .pipeline_models import ClinicalProfileConfig, PatientClinicalProfile
from .pre_visit_brief import PreVisitDiabetesBrief, generate_pre_visit_brief
from .risk import RiskAssessmentResult, RiskCalculator, RiskCalculatorConfig, assess_risk
from .trend_analysis import ClinicalTrendConfig, ClinicalTrendReport, analyze_clinical_trends

# ---------------------------------------------------------------------------
# v2 新增：calculator_id → Inputs 組裝邏輯（架構文件v2 3.14節「計算
# calculator_registry 中已註冊、輸入齊備的 Tier A/B calculator」的具體
# 落地）。
#
# ★★★ 鐵律5/鐵律6 落地 ★★★：任何 profile 上缺乏對應原始資料的欄位一律
# 傳 None，交給各 calculator 自己的 INSUFFICIENT_DATA 分支處理，本檔案
# 絕不猜測/估算數值去填補缺漏。
#
# ★ 已知資料缺口（架構文件v2 第5節 open_questions#11）：AST/ALT/Platelet/
# BNP/NT-proBNP 尚無確認的院內 LIS item_code 對照表，故 FIB4/
# BNP_NTPROBNP_HF_SCREEN 兩個 calculator 在本檔案目前只能組裝出年齡/
# CKD 等間接變數，核心檢驗值恆為 None（誠實回報 INSUFFICIENT_DATA，
# 不臆測代碼）。
# ---------------------------------------------------------------------------

# statin 屬藥理分類 C10AA（WHO ATC，鐵律2），非 MEDICATION_ATC_CLASS_MAP
# 涵蓋範圍（該表僅列糖尿病用藥類別），故獨立宣告。
_STATIN_ATC_PREFIX = "C10AA"


# ★ 修正（Codex 審閱發現）：以下 `_latest_*` helper 原本未過濾
# 「觀察/檢驗/檢查日期不可晚於 as_of_date」，未來日期的紀錄（測試資料誤植、
# 或系統間時鐘/回填順序問題）會被當成「最新」結果餵給 calculator，汙染
# 以 as_of_date 為基準的回溯計算。既有 v1 `care_gap.py`/
# `trend_analysis.py` 本來就有各自的 as_of 窗口檢查（例如
# `state.latest_lab_within()` 的 `0 <= (as_of-result_date).days`），本檔案
# 新增的 helper 現在統一補上同一道防線。


def _latest_ckd_assessment(profile: PatientClinicalProfile):
    candidates = [a for a in profile.enrollment_state.ckd_assessments if a.assessment_date <= profile.as_of_date]
    if not candidates:
        return None
    return max(candidates, key=lambda a: a.assessment_date)


def _latest_lab_value(profile: PatientClinicalProfile, item_codes: tuple[str, ...]):
    candidates = []
    for code in item_codes:
        series = profile.lab_series_by_item.get(code.upper())
        if series:
            # 每組已依 result_date 新到舊排序；取「不晚於 as_of_date」的第一筆。
            for lr in series:
                if lr.result_date <= profile.as_of_date:
                    candidates.append(lr)
                    break
    if not candidates:
        return None, None
    latest = max(candidates, key=lambda lr: lr.result_date)
    return latest.value, latest.result_date


def _latest_vital_sign(profile: PatientClinicalProfile):
    candidates = [v for v in profile.vital_signs if v.observation_date <= profile.as_of_date]
    if not candidates:
        return None
    return max(candidates, key=lambda v: v.observation_date)


def _latest_vascular_exam(profile: PatientClinicalProfile):
    candidates = [e for e in profile.vascular_exams if e.exam_date <= profile.as_of_date]
    if not candidates:
        return None
    return max(candidates, key=lambda e: e.exam_date)


def _latest_foot_neuro_exam(profile: PatientClinicalProfile):
    candidates = [e for e in profile.foot_neuro_exams if e.exam_date <= profile.as_of_date]
    if not candidates:
        return None
    return max(candidates, key=lambda e: e.exam_date)


def _latest_cardiac_imaging(profile: PatientClinicalProfile):
    candidates = [c for c in profile.cardiac_imaging if c.study_date <= profile.as_of_date]
    if not candidates:
        return None
    return max(candidates, key=lambda c: c.study_date)


def _has_drug_class(profile: PatientClinicalProfile, atc_prefixes: tuple[str, ...]) -> bool:
    codes_upper = {c.upper() for c in profile.active_medication_atc_codes}
    return any(any(c.startswith(p) for p in atc_prefixes) for c in codes_upper)


def _active_drug_classes(profile: PatientClinicalProfile) -> frozenset[str]:
    # 沿用 medication_intelligence.MEDICATION_ATC_CLASS_MAP 的糖尿病用藥
    # 分類，不重複宣告（鐵律7）。
    classes: set[str] = set()
    codes_upper = {c.upper() for c in profile.active_medication_atc_codes}
    for drug_class, prefixes in MEDICATION_ATC_CLASS_MAP.items():
        if any(any(c.startswith(p) for p in prefixes) for c in codes_upper):
            classes.add(drug_class)
    return frozenset(classes)


def _build_ckd_ga_inputs(profile: PatientClinicalProfile, complication_report: ComplicationReport) -> CKDGAInputs:
    assessment = _latest_ckd_assessment(profile)
    corroborating = any(f.category == "NEPHROPATHY" for f in complication_report.findings)
    return CKDGAInputs(
        patient_id=profile.patient_id,
        as_of=profile.as_of_date,
        egfr=assessment.egfr if assessment else None,
        uacr=assessment.uacr if assessment else None,  # ★ 只用真正的 UACR，不可混用 UPCR（量表不同，鐵律5）
        egfr_date=assessment.assessment_date if assessment else None,
        uacr_date=assessment.assessment_date if assessment else None,
        corroborating_ckd_diagnosis=corroborating,
    )


def _build_abi_tbi_inputs(profile: PatientClinicalProfile) -> ABITBIInputs:
    exam = _latest_vascular_exam(profile)
    if exam is None:
        return ABITBIInputs(patient_id=profile.patient_id, as_of=profile.as_of_date)
    return ABITBIInputs(
        patient_id=profile.patient_id,
        as_of=profile.as_of_date,
        abi_right=exam.abi_right,
        abi_left=exam.abi_left,
        tbi_right=exam.tbi_right,
        tbi_left=exam.tbi_left,
        measurement_date=exam.exam_date,
        claudication_present=exam.claudication_present,
        pedal_pulse_abnormal=(not exam.pedal_pulse_present) if exam.pedal_pulse_present is not None else None,
    )


def _derive_lops_present(exam) -> Optional[bool]:
    """★ 工程規則化詮釋（非規格逐字條文，需臨床端覆核）：LOPS
    （loss of protective sensation）常見臨床慣例以 monofilament 或
    vibration perception 任一項異常即判定陽性；`clinical_data_layer.
    FootNeuroExam` 本身刻意不含 LOPS 判定邏輯（見該檔案 docstring），本
    函式是本管線目前唯一的 LOPS 推導點。三項檢查皆為 not_tested 時回傳
    None（未評估，不可默視為陰性，鐵律6）。"""
    if exam is None:
        return None
    results = (exam.monofilament_result_left, exam.monofilament_result_right, exam.vibration_result)
    if any(r == "abnormal" for r in results):
        return True
    if all(r in ("normal", "not_tested") for r in results) and any(r == "normal" for r in results):
        return False
    return None  # 全部 not_tested，未評估


def _build_iwgdf_inputs(
    profile: PatientClinicalProfile,
    complication_report: ComplicationReport,
    abi_tbi_result: Optional[CalculatorResult],
) -> IWGDFFootInputs:
    exam = _latest_foot_neuro_exam(profile)
    lops_present = _derive_lops_present(exam)

    pad_present: Optional[bool] = None
    if abi_tbi_result is not None and abi_tbi_result.execution_status == CalculatorExecutionStatus.COMPUTED:
        pad_present = abi_tbi_result.clinical_status == ClinicalStatus.SUSPECTED

    ulcer_categories = {f.category for f in complication_report.findings}
    previous_foot_ulcer = "FOOT_ULCER_HISTORY" in ulcer_categories or bool(exam and exam.ulcer_history)
    previous_amputation = "AMPUTATION_HISTORY" in ulcer_categories or bool(exam and exam.amputation_history)

    return IWGDFFootInputs(
        patient_id=profile.patient_id,
        as_of=profile.as_of_date,
        lops_present=lops_present,
        pad_present=pad_present,
        foot_deformity_present=exam.foot_deformity_present if exam else None,
        previous_foot_ulcer=previous_foot_ulcer,
        previous_amputation=previous_amputation,
        kidney_failure_present=None,  # 由呼叫端 _compute_calculator_results() 於算完 KDIGO_GA 後回填
        last_foot_evaluation_date=exam.exam_date if exam else None,
    )


def _build_fib4_inputs(profile: PatientClinicalProfile) -> FIB4Inputs:
    return FIB4Inputs(
        patient_id=profile.patient_id,
        as_of=profile.as_of_date,
        age_years=profile.enrollment_state.age_years,
        # AST/ALT/Platelet 尚無確認之 LIS item_code（open_questions#11），
        # 誠實回報 None，不臆測代碼。
        ast_u_l=None,
        alt_u_l=None,
        platelet_10e9_l=None,
    )


def _build_bnp_inputs(profile: PatientClinicalProfile, complication_report: ComplicationReport) -> NatriureticPeptideInputs:
    vital = _latest_vital_sign(profile)
    has_ckd = any(f.category == "NEPHROPATHY" for f in complication_report.findings)
    return NatriureticPeptideInputs(
        patient_id=profile.patient_id,
        as_of=profile.as_of_date,
        # BNP/NT-proBNP 尚無確認之 LIS item_code（open_questions#11）。
        bnp_pg_ml=None,
        nt_probnp_pg_ml=None,
        has_ckd=has_ckd,
        age_years=profile.enrollment_state.age_years,
        has_obesity=None,
    )


def _build_ada_hypo_inputs(profile: PatientClinicalProfile) -> HypoglycemiaRiskFactorInputs:
    classes = _active_drug_classes(profile)
    return HypoglycemiaRiskFactorInputs(
        patient_id=profile.patient_id,
        as_of=profile.as_of_date,
        on_insulin="INSULIN" in classes,
        on_sulfonylurea="SULFONYLUREA" in classes,
        on_meglitinide="MEGLITINIDE" in classes,
        # major/minor risk factors 需結構化風險因子評估（例如近期低血糖病史
        # 問卷），本管線尚無此類資料來源，risk_factors_assessed 恆 False，
        # 不臆測（鐵律6）。
    )


def _build_watch_dm_inputs(profile: PatientClinicalProfile) -> WatchDmInputs:
    vital = _latest_vital_sign(profile)
    cardiac = _latest_cardiac_imaging(profile)
    assessment = _latest_ckd_assessment(profile)
    hdl, _ = _latest_lab_value(profile, ("09043C",))
    fpg, _ = _latest_lab_value(profile, ("09005C",))
    return WatchDmInputs(
        patient_id=profile.patient_id,
        as_of=profile.as_of_date,
        age_years=profile.enrollment_state.age_years,
        bmi=vital.bmi if vital else None,
        systolic_bp=vital.systolic_bp if vital else None,
        diastolic_bp=vital.diastolic_bp if vital else None,
        creatinine=None,  # dm_eligibility CKDAssessment 只存 egfr，不存原始肌酸酐數值
        hdl_c=hdl,
        fasting_plasma_glucose=fpg,
        qrs_duration_ms=cardiac.qrs_duration_ms if cardiac else None,
        previous_mi=None,  # 無專屬 MI/CABG 病史結構化欄位（僅有 CVD 大類 ICD 命中）
        previous_cabg=None,
    )


def _build_prevent_inputs(profile: PatientClinicalProfile, complication_report: ComplicationReport) -> PreventInputs:
    vital = _latest_vital_sign(profile)
    assessment = _latest_ckd_assessment(profile)
    exam = _latest_vascular_exam(profile)
    total_chol, _ = _latest_lab_value(profile, ("09001C",))
    hdl, _ = _latest_lab_value(profile, ("09043C",))
    hba1c, _ = _latest_lab_value(profile, ("09006C", "09139C"))
    has_revasc = bool(exam and exam.revascularization_history)
    return PreventInputs(
        patient_id=profile.patient_id,
        as_of=profile.as_of_date,
        age_years=profile.enrollment_state.age_years,
        systolic_bp=vital.systolic_bp if vital else None,
        total_cholesterol=total_chol,
        hdl_c=hdl,
        current_statin_treatment=_has_drug_class(profile, (_STATIN_ATC_PREFIX,)),
        smoking_status=vital.smoking_status.value if vital else None,
        egfr=assessment.egfr if assessment else None,
        bmi=vital.bmi if vital else None,
        diabetes_status=True,  # 本管線收案族群恆為糖尿病病人
        uacr=assessment.uacr if assessment else None,
        hba1c_latest=hba1c,
        complications=frozenset(f.category for f in complication_report.findings),
        has_revascularization_history=has_revasc,
    )


def _build_legacy_ascvd_inputs(profile: PatientClinicalProfile, complication_report: ComplicationReport) -> LegacyAscvdPceInputs:
    vital = _latest_vital_sign(profile)
    total_chol, _ = _latest_lab_value(profile, ("09001C",))
    hdl, _ = _latest_lab_value(profile, ("09043C",))
    exam = _latest_vascular_exam(profile)
    has_revasc = bool(exam and exam.revascularization_history)
    return LegacyAscvdPceInputs(
        patient_id=profile.patient_id,
        as_of=profile.as_of_date,
        age_years=profile.enrollment_state.age_years,
        systolic_bp=vital.systolic_bp if vital else None,
        total_cholesterol=total_chol,
        hdl_c=hdl,
        current_statin_treatment=_has_drug_class(profile, (_STATIN_ATC_PREFIX,)),
        smoking_status=vital.smoking_status.value if vital else None,
        treated_hypertension=None,  # 無專屬高血壓用藥ATC分類判斷（開放問題，需藥師覆核ATC範圍）
        race_ethnicity=None,  # ★ 倫理待裁定項（open_questions#5），本檔案不預設任何值
        complications=frozenset(f.category for f in complication_report.findings),
        has_revascularization_history=has_revasc,
    )


def _build_karter_inputs(
    profile: PatientClinicalProfile, kdigo_ga_result: Optional[CalculatorResult]
) -> KarterHypoglycemiaInputs:
    classes = _active_drug_classes(profile)
    ckd_severe: Optional[bool] = None
    if kdigo_ga_result is not None and kdigo_ga_result.execution_status == CalculatorExecutionStatus.COMPUTED:
        g_stage = (kdigo_ga_result.result_values or {}).get("g_stage")
        if g_stage is not None:
            ckd_severe = g_stage in ("G4", "G5")

    ed_visits: Optional[int] = None
    prior_hypo_ed: Optional[bool] = None
    if profile.encounter_utilization:
        # ★ 修正（Codex 審閱發現）：原本用 date(year-1, month, day) 手動
        # 減一年，遇到 as_of_date=2/29（閏年）且 year-1 非閏年時會直接
        # ValueError 崩潰；改用 timedelta(days=365) 與 pipeline_models.py
        # 既有 diagnosis_lookback_days/medication_lookback_days 同款 365天
        # 回溯窗口慣例一致（工程慣例，非規格逐字條文）。
        start = profile.as_of_date - timedelta(days=365)
        recent = [e for e in profile.encounter_utilization if start <= e.visit_date <= profile.as_of_date]
        ed_visits = sum(1 for e in recent if e.setting == "ed")
        # ★ 修正（Codex 審閱發現）：原本掃描 profile.encounter_utilization
        # 全部紀錄（無時間窗），與 Karter 工具「過去12個月」定義不符；改用
        # 上面已算好的 recent（同一份12個月窗口，鐵律7不重複開窗）。
        prior_hypo_ed = any(e.setting in ("ed", "inpatient") and e.hypoglycemia_related for e in recent)

    return KarterHypoglycemiaInputs(
        patient_id=profile.patient_id,
        as_of=profile.as_of_date,
        prior_hypo_related_ed_or_hosp=prior_hypo_ed,
        ed_visits_prior_12mo=ed_visits,
        insulin_use="INSULIN" in classes,
        sulfonylurea_use="SULFONYLUREA" in classes,
        ckd_stage_4_5_or_severe=ckd_severe,
        age_years=profile.enrollment_state.age_years,
    )


def _build_kfre_inputs(profile: PatientClinicalProfile) -> Kfre4VarInputs:
    assessment = _latest_ckd_assessment(profile)
    return Kfre4VarInputs(
        patient_id=profile.patient_id,
        as_of=profile.as_of_date,
        age_years=profile.enrollment_state.age_years,
        sex=profile.sex,
        egfr=assessment.egfr if assessment else None,
        uacr=assessment.uacr if assessment else None,
    )


def _compute_calculator_results(
    profile: PatientClinicalProfile,
    complication_report: ComplicationReport,
    registry: CalculatorRegistry,
) -> dict[str, CalculatorResult]:
    """對 `registry` 中已註冊的每個 calculator_id 組裝 Inputs 並執行。
    IWGDF_FOOT_RISK 依賴 KDIGO_GA（kidney_failure_present）與
    ABI_TBI_PAD_SCREEN（pad_present）的結果，故固定先算這兩者（鐵律7：
    IWGDF 不自己重算 CKD 分期/PAD screening，直接 reuse 同一次
    pipeline run 已算出的結果）。calculator_id 未登記對應 Inputs builder
    時（例如呼叫端自訂 registry 註冊了本檔案不認識的新 calculator）不會
    出現在回傳值中，不 raise（向下相容未來擴充）。"""
    results: dict[str, CalculatorResult] = {}
    registered_ids = set(registry.list_calculator_ids())

    if "KDIGO_GA" in registered_ids:
        results["KDIGO_GA"] = registry.compute("KDIGO_GA", _build_ckd_ga_inputs(profile, complication_report))

    if "ABI_TBI_PAD_SCREEN" in registered_ids:
        results["ABI_TBI_PAD_SCREEN"] = registry.compute("ABI_TBI_PAD_SCREEN", _build_abi_tbi_inputs(profile))

    if "IWGDF_FOOT_RISK" in registered_ids:
        iwgdf_inputs = _build_iwgdf_inputs(profile, complication_report, results.get("ABI_TBI_PAD_SCREEN"))
        kdigo_result = results.get("KDIGO_GA")
        if kdigo_result is not None and kdigo_result.execution_status == CalculatorExecutionStatus.COMPUTED:
            g_stage = (kdigo_result.result_values or {}).get("g_stage")
            if g_stage is not None:
                iwgdf_inputs = replace(iwgdf_inputs, kidney_failure_present=g_stage in ("G4", "G5"))
        results["IWGDF_FOOT_RISK"] = registry.compute("IWGDF_FOOT_RISK", iwgdf_inputs)

    if "FIB4" in registered_ids:
        results["FIB4"] = registry.compute("FIB4", _build_fib4_inputs(profile))

    if "BNP_NTPROBNP_HF_SCREEN" in registered_ids:
        results["BNP_NTPROBNP_HF_SCREEN"] = registry.compute(
            "BNP_NTPROBNP_HF_SCREEN", _build_bnp_inputs(profile, complication_report)
        )

    if "ADA_HYPO_L1" in registered_ids:
        results["ADA_HYPO_L1"] = registry.compute("ADA_HYPO_L1", _build_ada_hypo_inputs(profile))

    if "WATCH_DM" in registered_ids:
        results["WATCH_DM"] = registry.compute("WATCH_DM", _build_watch_dm_inputs(profile))

    if "PREVENT" in registered_ids:
        results["PREVENT"] = registry.compute("PREVENT", _build_prevent_inputs(profile, complication_report))

    if "ASCVD_PCE_2013" in registered_ids:
        results["ASCVD_PCE_2013"] = registry.compute(
            "ASCVD_PCE_2013", _build_legacy_ascvd_inputs(profile, complication_report)
        )

    if "KARTER_HYPO_ED_HOSP" in registered_ids:
        results["KARTER_HYPO_ED_HOSP"] = registry.compute(
            "KARTER_HYPO_ED_HOSP", _build_karter_inputs(profile, results.get("KDIGO_GA"))
        )

    if "KFRE_4VAR" in registered_ids:
        results["KFRE_4VAR"] = registry.compute("KFRE_4VAR", _build_kfre_inputs(profile))

    return results


@dataclass
class PipelineRunResult:
    """第1~7站的完整中間結果，供呼叫端（醫師 UI）取用，並在醫師決策完成
    後傳給 `finalize_pipeline()`。"""

    profile: PatientClinicalProfile
    trend_report: ClinicalTrendReport
    complication_report: ComplicationReport
    risk_result: RiskAssessmentResult
    calculator_results: dict[str, CalculatorResult]  # v2新增
    clinical_state: PatientClinicalState  # v2新增
    care_gap_report: CareGapReport
    care_gap_agent_report: CareGapAgentReport  # v2新增
    guideline_report: GuidelineRecommendationReport
    medication_report: MedicationIntelligenceReport  # v2新增
    decision_record: PhysicianDecisionRecord
    pre_visit_brief: PreVisitDiabetesBrief  # v2新增


def run_stages_1_to_7(
    state: PatientEnrollmentState,
    *,
    eligibility_report: EligibilityReport | None = None,
    physician: PhysicianStatus | None = None,
    codes_in_scope: Sequence[str] | None = None,
    eligibility_engine=None,
    profile_config: ClinicalProfileConfig | None = None,
    trend_config: ClinicalTrendConfig | None = None,
    complication_config: ComplicationConfig | None = None,
    risk_calculator: RiskCalculator | None = None,
    risk_config: RiskCalculatorConfig | None = None,
    care_gap_config: EligibilityConfig | None = None,
    include_quality_monitoring: bool = True,
    guideline_rules: Sequence[RecommendationRule] | None = None,
    # ↓ v2 新增，皆有預設值（架構文件v2 3.14節）↓
    calculator_registry: CalculatorRegistry | None = None,
    clinical_state_config: ClinicalStateConfig | None = None,
    medication_rules: Sequence[MedicationIndicationRule] | None = None,
    care_gap_agent_config: CareGapAgentConfig | None = None,
    alert_config: AlertClassificationConfig | None = None,
    # ↓ v2 新增（Codex 審閱發現的真實缺口的補正）：clinical_data_layer.py
    # 型別的透傳參數，皆預設空/None，向下相容既有呼叫端。缺了這批參數時
    # `_compute_calculator_results()` 組裝出的每個 calculator Inputs 恆缺
    # ABI/TBI、foot exam、vitals、cardiac imaging 等 Layer1 擴充資料，
    # 等同於整條 v2 calculator/CDS 流程只能吃到 v1 既有資料——見架構文件v2
    # 3.1節「與 pipeline_models.PatientClinicalProfile 的關係」。↓
    vital_signs: tuple = (),
    ophthalmology_findings: tuple = (),
    cardiac_imaging: tuple = (),
    foot_neuro_exams: tuple = (),
    vascular_exams: tuple = (),
    imaging_studies: tuple = (),
    hypoglycemia_events: tuple = (),
    procedures: tuple = (),
    encounter_utilization: tuple = (),
    administrative_status=None,
    data_source_registry=None,
    sex: str | None = None,
) -> PipelineRunResult:
    """依序執行第1~7站（資料整合→臨床趨勢→併發症辨識→風險計算→
    Care Gap→Guideline Recommendation→醫師決策紀錄初始化），回傳全部
    中間結果。第7站僅初始化一份全 PENDING 的決策紀錄，實際決策由醫師 UI
    逐筆呼叫 `decision_record.record_decision()`。

    `codes_in_scope` 未指定時，預設取 `profile.eligibility_report.results`
    的全部代碼（不論該代碼目前 eligible 與否——若無 eligibility_report
    則為空清單，並不視為錯誤——呼叫端此時可自行決定要評估哪些代碼的
    Care Gap）。刻意不用 `eligible_codes()`：一個代碼常常正是因為缺某項
    檢驗才 ineligible，用 eligible_codes() 當預設 scope 會讓那筆缺漏永遠
    不被檢查（Codex #3）。

    ★ v2 新增流程（既有 v1 流程不變，只是在其後追加）：計算
    `calculator_registry`（預設 `DEFAULT_CALCULATOR_REGISTRY`）中已註冊、
    輸入齊備的 Tier A/B calculator → `calculator_results`；
    `derive_clinical_state(...)` → `clinical_state`；`build_guideline_input()`
    改傳入 `clinical_state`/`calculator_results`；`present_for_decision()`
    改吃 `GuidelineRecommendation` ∪ `MedicationRecommendation`（見
    §3.8/3.9 `Reviewable`）；最後呼叫 `generate_pre_visit_brief()` 附掛在
    回傳物件。

    ★ 與規格pseudocode的一處刻意偏離（Codex 審閱發現）：規格pseudocode
    在本函式簽名列出 `order_source: PendingOrderSource | None = None`，
    但第1~7站流程本身沒有任何步驟會用到已開立醫令查詢（§28 醫令完成度
    追蹤是 `finalize_pipeline()` 的職責，該函式已正確接受並使用
    `order_source`）——把一個完全不會被使用的參數留在簽名上會誤導呼叫端
    以為傳了就有效，故本函式不接受此參數，需要時請在
    `finalize_pipeline(..., order_source=...)` 傳入。"""
    profile = build_patient_clinical_profile(
        state,
        eligibility_report=eligibility_report,
        physician=physician,
        config=profile_config,
        eligibility_engine=eligibility_engine,
        vital_signs=vital_signs,
        ophthalmology_findings=ophthalmology_findings,
        cardiac_imaging=cardiac_imaging,
        foot_neuro_exams=foot_neuro_exams,
        vascular_exams=vascular_exams,
        imaging_studies=imaging_studies,
        hypoglycemia_events=hypoglycemia_events,
        procedures=procedures,
        encounter_utilization=encounter_utilization,
        administrative_status=administrative_status,
        data_source_registry=data_source_registry,
        sex=sex,
    )
    trend_report = analyze_clinical_trends(profile, trend_config)
    complication_report = identify_complications(profile, complication_config)
    risk_result = assess_risk(profile, trend_report, complication_report, calculator=risk_calculator, config=risk_config)

    if codes_in_scope is None:
        # ★ 修正（Codex #3）：先前預設用 eligible_codes()——只有「已達成
        # 資格」的代碼才會被送進 assess_care_gaps()。但一個代碼之所以還不
        # eligible，往往正是因為缺了某項檢驗；用 eligible_codes() 當預設
        # scope，等於系統性地把「這位病人缺哪些檢驗才能達成資格」這個
        # care gap 最有價值的用途整個隱藏掉——P1408C 因為缺 09005C 而
        # ineligible 時，P1408C 根本不在 eligible_codes() 裡，於是那筆缺漏
        # 永遠不會被 assess_care_gaps() 檢查、更不會出現在
        # deduplicated_missing_items。改為取 EligibilityReport.results 的
        # 全部代碼（不論 eligible 與否）——這正是 EligibilityEngine.
        # evaluate() 一律會評估的固定代碼集合，care gap 分析本就該涵蓋。
        codes_in_scope = [r.code for r in profile.eligibility_report.results] if profile.eligibility_report else []
    care_gap_report = assess_care_gaps(
        profile, codes_in_scope, config=care_gap_config, include_quality_monitoring=include_quality_monitoring
    )

    if calculator_registry is not None:
        registry = calculator_registry
    else:
        # ★ DEFAULT_CALCULATOR_REGISTRY 本身是空的（calculators/__init__.py
        # 刻意不在 import 時產生副作用性註冊，見該檔案 register_default_
        # calculators() docstring）；呼叫端未自訂 registry 時，本函式代為
        # 確保預設 11 個 Tier A/B calculator 皆已註冊（冪等：重複呼叫
        # register_default_calculators() 只是覆蓋同一組 calculator_id/
        # version key，無副作用累積）。
        register_default_calculators(DEFAULT_CALCULATOR_REGISTRY)
        registry = DEFAULT_CALCULATOR_REGISTRY
    calculator_results = _compute_calculator_results(profile, complication_report, registry)

    clinical_state = derive_clinical_state(
        profile,
        complication_report,
        care_gap_report,
        risk_result,
        calculator_results=calculator_results,
        config=clinical_state_config,
    )

    care_gap_agent_report = assess_care_gap_agent(
        profile, clinical_state, care_gap_report, calculator_results=calculator_results, config=care_gap_agent_config
    )

    guideline_input = build_guideline_input(
        profile,
        trend_report,
        complication_report,
        risk_result,
        care_gap_report,
        clinical_state=clinical_state,
        calculator_results=calculator_results,
    )
    engine = GuidelineRecommendationEngine(guideline_rules)
    guideline_report = engine.build(guideline_input)

    medication_check_input = build_medication_check_input(profile, clinical_state, calculator_results)
    medication_report = build_medication_intelligence_report(medication_check_input, rules=medication_rules)

    decision_record = present_for_decision(
        [*guideline_report.recommendations, *medication_report.recommendations],
        patient_id=profile.patient_id,
        as_of_date=profile.as_of_date,
    )

    pre_visit_brief = generate_pre_visit_brief(
        profile, trend_report, clinical_state, calculator_results, guideline_report, decision_record, alert_config=alert_config
    )

    return PipelineRunResult(
        profile=profile,
        trend_report=trend_report,
        complication_report=complication_report,
        risk_result=risk_result,
        calculator_results=calculator_results,
        clinical_state=clinical_state,
        care_gap_report=care_gap_report,
        care_gap_agent_report=care_gap_agent_report,
        guideline_report=guideline_report,
        medication_report=medication_report,
        decision_record=decision_record,
        pre_visit_brief=pre_visit_brief,
    )


@dataclass
class PipelineFinalResult:
    education_plan: EducationPlan  # v1既有，保留
    education_report: PatientEducationReport  # v2新增
    followup_plan: FollowUpPlan  # v1既有，保留
    order_tracking_report: OrderTrackingReport  # v2新增


def finalize_pipeline(
    run_result: PipelineRunResult,
    decision_record: PhysicianDecisionRecord | None = None,
    *,
    education_config: EducationTopicMappingConfig | None = None,
    followup_config: EligibilityConfig | None = None,
    complication_monitoring_config: ComplicationMonitoringConfig | None = None,
    assume_eligible_codes_claimed_today: bool = True,
    # ↓ v2 新增，皆有預設值（架構文件v2 3.14節）↓
    education_report_config: EducationReportBuilderConfig | None = None,
    order_source: PendingOrderSource | None = None,
    order_tracking_config: OrderTrackingConfig | None = None,
) -> PipelineFinalResult:
    """醫師完成決策後呼叫：跑第8站（病人衛教）與第9站（後續追蹤）。

    `decision_record` 省略時預設使用 `run_result.decision_record`（呼叫端
    應已在其上呼叫過 `record_decision()`）；顯式傳入則允許呼叫端使用另一份
    （例如從資料庫重新載入的）決策紀錄。

    ★ v2 新增：`order_source` 未提供時（預設 `None`），
    `order_tracking_report.warnings` 記錄「未串接HIS醫令查詢介面」，不阻斷
    既有 education/followup 邏輯（架構文件v2 3.12節）。

    ★ 修正（Codex 審閱發現）：顯式傳入 `decision_record` 時，本函式先前
    未驗證它與 `run_result.profile` 屬於同一位病人/同一次評估——若呼叫端
    不慎傳入另一位病人（或另一個 as_of_date）的決策紀錄，會被直接拿去跟
    `run_result.profile`/`run_result.complication_report` 混合產生衛教/
    追蹤計畫，形成跨病人資料汙染且無任何錯誤提示。現在顯式檢查
    `patient_id`/`as_of_date` 是否一致，不一致直接 raise，拒絕靜默混用
    （鐵律5/鐵律6：資料不一致必須顯式失敗，不可靜默假設）。"""
    record = decision_record if decision_record is not None else run_result.decision_record
    if decision_record is not None and (
        decision_record.patient_id != run_result.profile.patient_id
        or decision_record.as_of_date != run_result.profile.as_of_date
    ):
        raise DecisionValidationError(
            f"finalize_pipeline(): 傳入的 decision_record（patient_id={decision_record.patient_id!r}, "
            f"as_of_date={decision_record.as_of_date!r}）與 run_result.profile"
            f"（patient_id={run_result.profile.patient_id!r}, as_of_date={run_result.profile.as_of_date!r}）"
            "不一致，拒絕混用不同病人/評估基準日的資料"
        )

    education_plan = select_education_topics(record, run_result.complication_report, education_config)
    followup_plan = compute_follow_up_plan(
        run_result.profile,
        run_result.complication_report,
        decision_record=record,
        config=followup_config,
        complication_monitoring_config=complication_monitoring_config,
        assume_eligible_codes_claimed_today=assume_eligible_codes_claimed_today,
    )

    order_tracking_report = track_pending_orders(
        run_result.profile, run_result.profile.as_of_date, order_source, config=order_tracking_config
    )
    education_report = generate_patient_education_report(
        run_result.clinical_state,
        run_result.trend_report,
        run_result.complication_report,
        record,
        pending_orders=order_tracking_report.pending_orders,
        config=education_report_config,
        # ★ 修正（Codex 審閱發現）：先前未把 education_config 傳入本函式，
        # 導致 education_report.resource_topics 用預設設定重算，與
        # education_plan.topics（上面已正確套用 education_config）結果
        # 不一致。
        topic_mapping_config=education_config,
    )

    return PipelineFinalResult(
        education_plan=education_plan,
        education_report=education_report,
        followup_plan=followup_plan,
        order_tracking_report=order_tracking_report,
    )
