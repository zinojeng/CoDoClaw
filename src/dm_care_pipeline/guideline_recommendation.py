"""
【第6站】Guideline Recommendation — 依前面各站的報告物件，比對一組可插拔
的規則（`RecommendationRule`），產出「有明確依據欄位」的建議清單。

每條建議都必須附上 `evidence`（來源型別 + 出處字串），且明確標示
`trigger_grounded_in_spec`（觸發條件本身是否可回溯規格書/通用醫學編碼）
與 `action_is_placeholder_content`（建議的處置動作文字是否為工程佔位、
未經臨床審閱）。本站只產生「建議」，不做任何決策（決策支援 vs 自動決策
的界線見第7站 physician_decision.py 的鐵律3註記）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Callable, Literal, Mapping, Optional, Sequence, TYPE_CHECKING

from dm_eligibility import rules_p7

from .calculators.base import CalculatorExecutionStatus, CalculatorResult
from .care_gap import CareGapItem, CareGapReport
from .clinical_data_object import ClinicalStatus
from .complication_identification import ComplicationFinding, ComplicationReport
from .pipeline_models import PatientClinicalProfile
from .risk import RiskAssessmentResult, RiskFactorContribution, RiskLevel
from .trend_analysis import ClinicalTrendReport, MarkerTrend, QualityMetricTier, QualityThresholdConfig

if TYPE_CHECKING:  # pragma: no cover - 型別提示用，避免執行期循環 import
    from .clinical_state import PatientClinicalState


class EvidenceType(str, Enum):
    CARE_GAP = "CARE_GAP"
    COMPLICATION = "COMPLICATION"
    RISK_FACTOR = "RISK_FACTOR"
    QUALITY_METRIC = "QUALITY_METRIC"
    QUALITY_MONITORING_ALERT = "QUALITY_MONITORING_ALERT"
    CALCULATOR_RESULT = "CALCULATOR_RESULT"  # v2 新增：Layer3 calculator（Tier A/B）輸出作為證據來源


@dataclass(frozen=True)
class RecommendationEvidence:
    evidence_type: EvidenceType
    source_id: str
    detail: str
    spec_reference: Optional[str]


class RecommendationPriority(str, Enum):
    ROUTINE = "ROUTINE"
    PRIORITY = "PRIORITY"
    URGENT = "URGENT"


@dataclass(frozen=True)
class GuidelineSource:
    guideline_id: str
    version: str
    publisher_or_authority: str
    citation: str
    last_updated: Optional[date] = None


# 規格§15逐字8項登錄。version 除 ADA_SOC_2026/IWGDF_2023/Taiwan_NHI_*_2026
# 已內嵌年份外，KDIGO/AHA_ACC/Taiwan_DM_Guideline_2022/Taiwan_DKD_2024 之
# version 欄位留待人工提供正式版次（見架構文件v2 第4/5節 open_questions#14）。
GUIDELINE_LIBRARY: dict[str, GuidelineSource] = {
    "ADA_SOC_2026": GuidelineSource(
        "ADA_SOC_2026", "2026", "American Diabetes Association", "ADA Standards of Care in Diabetes 2026"
    ),
    "Taiwan_DM_Guideline_2022": GuidelineSource("Taiwan_DM_Guideline_2022", "2022", "台灣糖尿病學會", ""),
    "Taiwan_DKD_2024": GuidelineSource("Taiwan_DKD_2024", "2024", "台灣腎臟醫學會", ""),
    "KDIGO": GuidelineSource("KDIGO", "", "Kidney Disease: Improving Global Outcomes", ""),
    "AHA_ACC": GuidelineSource("AHA_ACC", "", "American Heart Association / ACC", ""),
    "IWGDF_2023": GuidelineSource("IWGDF_2023", "2023", "International Working Group on the Diabetic Foot", ""),
    "Taiwan_NHI_DM_P4P_2026": GuidelineSource(
        "Taiwan_NHI_DM_P4P_2026", "2026", "衛生福利部中央健康保險署", "P14 spec"
    ),
    "Taiwan_NHI_CKD_P4P_2026": GuidelineSource(
        "Taiwan_NHI_CKD_P4P_2026", "2026", "衛生福利部中央健康保險署", "P7 spec"
    ),
}


@dataclass(frozen=True)
class GuidelineRecommendation:
    recommendation_id: str
    rule_id: str
    title: str
    rationale: str
    evidence: tuple[RecommendationEvidence, ...]
    priority: RecommendationPriority
    trigger_grounded_in_spec: bool
    action_is_placeholder_content: bool
    education_topic_code: Optional[str] = None  # 掛鉤第8站，內容由第8站owner定義
    # --- v2 新增（架構文件v2 3.7節）。規格§30要求「結果物件」上也要有
    # guideline/version，不只在規則定義上，故與 RecommendationRule 同步擴充
    # 相同欄位。---
    guideline_id: Optional[str] = None
    recommendation_number: Optional[str] = None
    evidence_level: Optional[str] = None  # "TODO-SPEC-VERIFY" 占位，待臨床查證ADA正式grade
    applicable_population: Optional[str] = None
    exclusion: Optional[str] = None
    alert_level: Literal["information", "clinical_attention", "safety_alert"] = "clinical_attention"
    # 本 Layer 規則刻意不產生 safety_alert（保留給未來 Medication Agent）。
    related_finding_id: Optional[str] = None  # 指向 ClinicalFinding.finding_id，供 compose_clinical_data_objects() join


@dataclass
class GuidelineRecommendationReport:
    patient_id: str
    as_of_date: date
    recommendations: list[GuidelineRecommendation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GuidelineRecommendationInput:
    """Guideline 引擎的內部標準輸入。由 `build_guideline_input()` 從
    第2~5站實際報告物件轉譯而成。"""

    patient_id: str
    as_of_date: date
    care_gaps: tuple[CareGapItem, ...] = ()
    complications: tuple[ComplicationFinding, ...] = ()
    risk_contributions: tuple[RiskFactorContribution, ...] = ()
    overall_risk_level: RiskLevel = RiskLevel.UNKNOWN
    is_placeholder_risk_methodology: bool = True
    marker_trends: tuple[MarkerTrend, ...] = ()
    quality_monitoring_alerts: tuple[str, ...] = ()
    # --- v2 新增（架構文件v2 3.7節）：既有4欄位不動，向下相容既有呼叫端。---
    clinical_state: Optional["PatientClinicalState"] = None
    calculator_results: Mapping[str, CalculatorResult] = field(default_factory=dict)


def build_guideline_input(
    profile: PatientClinicalProfile,
    trend_report: ClinicalTrendReport,
    complication_report: ComplicationReport,
    risk_result: RiskAssessmentResult,
    care_gap_report: CareGapReport,
    *,
    clinical_state: Optional["PatientClinicalState"] = None,
    calculator_results: Optional[Mapping[str, CalculatorResult]] = None,
) -> GuidelineRecommendationInput:
    """整合新增的組裝函式：忠實轉譯第2~5站的實際輸出。
    `quality_monitoring_alerts` 直接取
    `profile.eligibility_report.quality_monitoring_alerts`（若非 None）。
    v2 新增的 `clinical_state`/`calculator_results` 皆為 keyword-only、預設
    None/空 dict，既有呼叫端零改動即可運作（向下相容）。"""
    quality_monitoring_alerts: tuple[str, ...] = ()
    if profile.eligibility_report is not None:
        quality_monitoring_alerts = tuple(profile.eligibility_report.quality_monitoring_alerts)

    return GuidelineRecommendationInput(
        patient_id=profile.patient_id,
        as_of_date=profile.as_of_date,
        care_gaps=tuple(care_gap_report.deduplicated_missing_items),
        complications=tuple(complication_report.findings),
        risk_contributions=tuple(risk_result.contributions),
        overall_risk_level=risk_result.overall_risk_level,
        is_placeholder_risk_methodology=risk_result.is_placeholder_methodology,
        marker_trends=tuple(trend_report.marker_trends),
        quality_monitoring_alerts=quality_monitoring_alerts,
        clinical_state=clinical_state,
        calculator_results=calculator_results or {},
    )


@dataclass(frozen=True)
class RecommendationRule:
    rule_id: str
    title_template: str
    priority: RecommendationPriority
    trigger_grounded_in_spec: bool
    action_is_placeholder_content: bool
    spec_reference: Optional[str]
    matcher: Callable[[GuidelineRecommendationInput], list[RecommendationEvidence]]
    education_topic_code: Optional[str] = None
    # --- v2 新增（架構文件v2 3.7節），與 GuidelineRecommendation 同步擴充。---
    guideline_id: Optional[str] = None
    recommendation_number: Optional[str] = None
    evidence_level: Optional[str] = None
    applicable_population: Optional[str] = None
    exclusion: Optional[str] = None
    alert_level: Literal["information", "clinical_attention", "safety_alert"] = "clinical_attention"
    related_finding_id_matcher: Optional[Callable[[GuidelineRecommendationInput], Optional[str]]] = None
    # 命中時回傳對應 ClinicalFinding.finding_id，供 compose_clinical_data_objects() join。


def _hba1c_poor_matcher(inp: GuidelineRecommendationInput) -> list[RecommendationEvidence]:
    evidence: list[RecommendationEvidence] = []
    for mt in inp.marker_trends:
        if mt.marker_name == "HBA1C" and mt.control_tier == QualityMetricTier.POOR:
            evidence.append(
                RecommendationEvidence(
                    evidence_type=EvidenceType.QUALITY_METRIC,
                    source_id="HBA1C",
                    detail=f"最新HbA1c={mt.latest_value}（{mt.latest_result_date}），依 trend_analysis.QualityThresholdConfig 分類為 POOR",
                    spec_reference="trend_analysis.QualityThresholdConfig（TODO-SPEC-VERIFY，任務指示採用值，非規格書逐字條文）",
                )
            )
    return evidence


def _ldl_poor_matcher(inp: GuidelineRecommendationInput) -> list[RecommendationEvidence]:
    evidence: list[RecommendationEvidence] = []
    for mt in inp.marker_trends:
        if mt.marker_name == "LDL" and mt.control_tier == QualityMetricTier.POOR:
            evidence.append(
                RecommendationEvidence(
                    evidence_type=EvidenceType.QUALITY_METRIC,
                    source_id="LDL",
                    detail=f"最新LDL={mt.latest_value}（{mt.latest_result_date}），依 trend_analysis.QualityThresholdConfig 分類為 POOR",
                    spec_reference="trend_analysis.QualityThresholdConfig（TODO-SPEC-VERIFY，任務指示採用值，非規格書逐字條文）",
                )
            )
    return evidence


def _nmrp_gap_matcher(inp: GuidelineRecommendationInput) -> list[RecommendationEvidence]:
    evidence: list[RecommendationEvidence] = []
    for alert in inp.quality_monitoring_alerts:
        if "NMRP" in alert or "眼" in alert:
            evidence.append(
                RecommendationEvidence(
                    evidence_type=EvidenceType.QUALITY_MONITORING_ALERT,
                    source_id="NMRP",
                    detail=alert,
                    spec_reference="P14 spec (c)(d) 品質監測180天強制檢驗排程",
                )
            )
    return evidence


def _renal_high_risk_matcher(inp: GuidelineRecommendationInput) -> list[RecommendationEvidence]:
    """RENAL_COMPLICATION_HIGH_RISK：AND 條件——「已辨識腎併發症」且
    「風險計算階段判定 ckd_stage 因子為 HIGH」皆成立才觸發。第二個條件
    來自 illustrative placeholder 風險計算模型，故本規則
    `trigger_grounded_in_spec=False`。"""
    nephropathy_finding = next((f for f in inp.complications if f.category == "NEPHROPATHY"), None)
    ckd_high_contribution = next(
        (c for c in inp.risk_contributions if c.factor == "ckd_stage" and c.level == RiskLevel.HIGH), None
    )
    if nephropathy_finding is None or ckd_high_contribution is None:
        return []
    return [
        RecommendationEvidence(
            evidence_type=EvidenceType.COMPLICATION,
            source_id="NEPHROPATHY",
            detail=f"併發症辨識命中腎併發症，ICD-10碼={nephropathy_finding.matched_icd10_codes}，CKD分期={nephropathy_finding.ckd_stage}",
            spec_reference="鐵律2：ICD-10-CM通用分類慣例（E*.2x/N18）",
        ),
        RecommendationEvidence(
            evidence_type=EvidenceType.RISK_FACTOR,
            source_id="ckd_stage",
            detail=ckd_high_contribution.value_summary,
            spec_reference=None,  # risk.RuleBasedRiskCalculator 為 illustrative placeholder，無規格出處
        ),
    ]


# ---------------------------------------------------------------------------
# v2 新增 Tier A 規則（架構文件v2 3.7節）：皆讀 `inp.calculator_results`，
# 消費既有 `calculators/` 輸出（不重算），`trigger_grounded_in_spec=True`。
# ---------------------------------------------------------------------------


def _computed_result(inp: GuidelineRecommendationInput, calculator_id: str) -> Optional[CalculatorResult]:
    result = inp.calculator_results.get(calculator_id)
    if result is None or result.execution_status != CalculatorExecutionStatus.COMPUTED:
        return None
    return result


def _kdigo_ga_severity_display_matcher(inp: GuidelineRecommendationInput) -> list[RecommendationEvidence]:
    """★ 只在 `clinical_status` 非 None（異常）時顯示，避免對 G1A1 正常
    分期的病人也產生一條建議（工程規則化詮釋，非規格逐字條文）。"""
    result = _computed_result(inp, "KDIGO_GA")
    if result is None or result.clinical_status is None:
        return []
    return [
        RecommendationEvidence(
            evidence_type=EvidenceType.CALCULATOR_RESULT,
            source_id="KDIGO_GA",
            detail=result.result_summary or "",
            spec_reference=result.spec_reference,
        )
    ]


def _fib4_secondary_assessment_matcher(inp: GuidelineRecommendationInput) -> list[RecommendationEvidence]:
    result = _computed_result(inp, "FIB4")
    if result is None or result.clinical_status != ClinicalStatus.SUSPECTED:
        return []
    return [
        RecommendationEvidence(
            evidence_type=EvidenceType.CALCULATOR_RESULT,
            source_id="FIB4",
            detail=f"{result.result_summary}；{result.action}",
            spec_reference=result.spec_reference,
        )
    ]


def _bnp_abnormal_echo_referral_matcher(inp: GuidelineRecommendationInput) -> list[RecommendationEvidence]:
    result = _computed_result(inp, "BNP_NTPROBNP_HF_SCREEN")
    if result is None or result.clinical_status != ClinicalStatus.SUSPECTED:
        return []
    return [
        RecommendationEvidence(
            evidence_type=EvidenceType.CALCULATOR_RESULT,
            source_id="BNP_NTPROBNP_HF_SCREEN",
            detail=f"{result.result_summary}；{result.action}",
            spec_reference=result.spec_reference,
        )
    ]


def _abi_tbi_pad_pathway_matcher(inp: GuidelineRecommendationInput) -> list[RecommendationEvidence]:
    result = _computed_result(inp, "ABI_TBI_PAD_SCREEN")
    if result is None or result.clinical_status != ClinicalStatus.SUSPECTED:
        return []
    return [
        RecommendationEvidence(
            evidence_type=EvidenceType.CALCULATOR_RESULT,
            source_id="ABI_TBI_PAD_SCREEN",
            detail=f"{result.result_summary}；{result.action}",
            spec_reference=result.spec_reference,
        )
    ]


def _iwgdf_foot_frequency_reminder_matcher(inp: GuidelineRecommendationInput) -> list[RecommendationEvidence]:
    """規格§10 追蹤頻率提醒——不論 Category 高低皆提醒下次追蹤頻率
    （COMPUTED 即觸發，非僅異常時才觸發，因這是排程提醒而非疾病警示）。"""
    result = _computed_result(inp, "IWGDF_FOOT_RISK")
    if result is None:
        return []
    return [
        RecommendationEvidence(
            evidence_type=EvidenceType.CALCULATOR_RESULT,
            source_id="IWGDF_FOOT_RISK",
            detail=f"{result.result_summary}；{result.action}",
            spec_reference=result.spec_reference,
        )
    ]


# P7001/P7002 必要檢驗項目代碼（含 GA 替代前不重複硬編，直接 reuse
# rules_p7.py，鐵律7）；只取 alternatives 供比對，不重抄天數視窗。
_NHI_CKD_P4P_LAB_ITEM_CODES: frozenset[str] = frozenset(
    code.upper()
    for req in (*rules_p7.P7001_LAB_REQUIREMENTS_BASE, *rules_p7.P7002_LAB_REQUIREMENTS_BASE)
    for code in req.alternatives
)


def _nhi_ckd_p4p_lab_gap_matcher(inp: GuidelineRecommendationInput) -> list[RecommendationEvidence]:
    """直接 reuse `rules_p7.P7001_LAB_REQUIREMENTS_BASE`/
    `P7002_LAB_REQUIREMENTS_BASE`（鐵律7：不重抄檢驗代碼本身），對
    `inp.care_gaps`（`CareGapReport.deduplicated_missing_items`）做交集比對。"""
    evidence: list[RecommendationEvidence] = []
    for item in inp.care_gaps:
        codes_upper = {c.upper() for c in item.source_codes}
        if codes_upper & _NHI_CKD_P4P_LAB_ITEM_CODES:
            evidence.append(
                RecommendationEvidence(
                    evidence_type=EvidenceType.CARE_GAP,
                    source_id="/".join(item.source_codes),
                    detail=f"{item.requirement.description}：P7（糖尿病合併早期腎病變）照護品質必要檢驗項目缺漏",
                    spec_reference=item.spec_reference,
                )
            )
    return evidence


# ---------------------------------------------------------------------------
# v2 新增 Tier B「僅資訊揭露」規則：只在 execution_status==
# REQUIRES_EXTERNAL_VALIDATED_MODEL 時觸發，alert_level="information"，
# evidence_level="risk_communication_only_pending_local_validation"，文字
# 禁止帶百分比門檻式建議（鐵律2）。
# ---------------------------------------------------------------------------


def _tier_b_info_matcher(inp: GuidelineRecommendationInput, calculator_id: str) -> list[RecommendationEvidence]:
    result = inp.calculator_results.get(calculator_id)
    if result is None or result.execution_status != CalculatorExecutionStatus.REQUIRES_EXTERNAL_VALIDATED_MODEL:
        return []
    provided = [f.name for f in result.inputs if f.provided]
    missing = list(result.missing_inputs)
    detail = (
        f"{calculator_id}：已提供變數 {provided or '（無）'}；缺漏變數 {missing or '（無）'}；"
        "本地驗證前僅供風險溝通參考，不得作為決策依據（規格§37）"
    )
    return [
        RecommendationEvidence(
            evidence_type=EvidenceType.CALCULATOR_RESULT,
            source_id=calculator_id,
            detail=detail,
            spec_reference=result.spec_reference,
        )
    ]


def _watch_dm_info_matcher(inp: GuidelineRecommendationInput) -> list[RecommendationEvidence]:
    return _tier_b_info_matcher(inp, "WATCH_DM")


def _prevent_info_matcher(inp: GuidelineRecommendationInput) -> list[RecommendationEvidence]:
    return _tier_b_info_matcher(inp, "PREVENT")


def _karter_info_matcher(inp: GuidelineRecommendationInput) -> list[RecommendationEvidence]:
    return _tier_b_info_matcher(inp, "KARTER_HYPO_ED_HOSP")


def _kfre_info_matcher(inp: GuidelineRecommendationInput) -> list[RecommendationEvidence]:
    return _tier_b_info_matcher(inp, "KFRE_4VAR")


def default_recommendation_rules(quality_cfg: QualityThresholdConfig | None = None) -> list[RecommendationRule]:
    """v1 既有 4 條示範規則 + v2 新增 6 條 Tier A 規則 + 4 條 Tier B
    僅資訊揭露規則，共 14 條（架構文件v2 3.7節，既有 4 條不刪、不改行為）。
    `quality_cfg` 目前僅供未來擴充傳遞自訂切點顯示用途，實際分類判斷已由
    `trend_analysis.analyze_clinical_trends()` 完成
    （`MarkerTrend.control_tier`），本函式不重新計算切點。"""
    return [
        RecommendationRule(
            rule_id="HBA1C_POOR_NO_RECENT_TRACKING",
            title_template="HbA1c控制不良，建議加強血糖追蹤與衛教",
            priority=RecommendationPriority.PRIORITY,
            trigger_grounded_in_spec=True,
            action_is_placeholder_content=True,
            spec_reference="trend_analysis.QualityThresholdConfig（TODO-SPEC-VERIFY）",
            matcher=_hba1c_poor_matcher,
            education_topic_code="GLYCEMIC_CONTROL_BASIC",
        ),
        RecommendationRule(
            rule_id="LDL_POOR_CONTROL",
            title_template="LDL控制不良，建議評估調整降血脂治療",
            priority=RecommendationPriority.PRIORITY,
            trigger_grounded_in_spec=True,
            action_is_placeholder_content=True,
            spec_reference="trend_analysis.QualityThresholdConfig（TODO-SPEC-VERIFY）",
            matcher=_ldl_poor_matcher,
            education_topic_code=None,
        ),
        RecommendationRule(
            rule_id="NMRP_GAP_180D",
            title_template="眼底檢查(NMRP)逾180天未執行，建議安排眼科追蹤",
            priority=RecommendationPriority.PRIORITY,
            trigger_grounded_in_spec=True,
            action_is_placeholder_content=True,
            spec_reference="P14 spec (c)(d)",
            matcher=_nmrp_gap_matcher,
            education_topic_code="EYE_EXAM_BASIC",
        ),
        RecommendationRule(
            rule_id="RENAL_COMPLICATION_HIGH_RISK",
            title_template="腎併發症合併風險計算階段判定之高風險CKD分期，建議轉診腎臟科評估",
            priority=RecommendationPriority.URGENT,
            trigger_grounded_in_spec=False,
            action_is_placeholder_content=True,
            spec_reference=None,
            matcher=_renal_high_risk_matcher,
            education_topic_code="RENAL_DIET_BASIC",
        ),
        # --- v2 新增 Tier A 規則（架構文件v2 3.7節），皆 trigger_grounded_in_spec=True ---
        RecommendationRule(
            rule_id="KDIGO_GA_SEVERITY_DISPLAY",
            title_template="CKD G/A 分期（KDIGO）",
            priority=RecommendationPriority.PRIORITY,
            trigger_grounded_in_spec=True,
            action_is_placeholder_content=False,
            spec_reference="OpenClaw for Diabetes HIS.md §6.1；KDIGO 國際標準分期表",
            matcher=_kdigo_ga_severity_display_matcher,
            guideline_id="KDIGO",
            alert_level="information",
        ),
        RecommendationRule(
            rule_id="FIB4_SECONDARY_ASSESSMENT",
            title_template="FIB-4 提示 advanced fibrosis risk，建議安排第二階段評估",
            priority=RecommendationPriority.PRIORITY,
            trigger_grounded_in_spec=True,
            action_is_placeholder_content=False,
            spec_reference="OpenClaw for Diabetes HIS.md §6.2",
            matcher=_fib4_secondary_assessment_matcher,
            education_topic_code="MASLD_MASH_BASIC",
        ),
        RecommendationRule(
            rule_id="BNP_ABNORMAL_ECHO_REFERRAL",
            title_template="Natriuretic peptide screening 異常，建議安排心臟超音波",
            priority=RecommendationPriority.PRIORITY,
            trigger_grounded_in_spec=True,
            action_is_placeholder_content=False,
            spec_reference="OpenClaw for Diabetes HIS.md §6.4",
            matcher=_bnp_abnormal_echo_referral_matcher,
            education_topic_code="HEART_FAILURE_BASIC",
        ),
        RecommendationRule(
            rule_id="ABI_TBI_PAD_PATHWAY",
            title_template="ABI/TBI PAD screening 異常，建議整合血管評估後續處置",
            priority=RecommendationPriority.PRIORITY,
            trigger_grounded_in_spec=True,
            action_is_placeholder_content=False,
            spec_reference="OpenClaw for Diabetes HIS.md §6.5",
            matcher=_abi_tbi_pad_pathway_matcher,
            education_topic_code="PAD_BASIC",
        ),
        RecommendationRule(
            rule_id="IWGDF_FOOT_FREQUENCY_REMINDER",
            title_template="足部評估追蹤頻率提醒（IWGDF）",
            priority=RecommendationPriority.PRIORITY,
            trigger_grounded_in_spec=True,
            action_is_placeholder_content=False,
            spec_reference="OpenClaw for Diabetes HIS.md §10",
            matcher=_iwgdf_foot_frequency_reminder_matcher,
            education_topic_code="FOOT_CARE_BASIC",
            guideline_id="IWGDF_2023",
        ),
        RecommendationRule(
            rule_id="NHI_CKD_P4P_LAB_GAP",
            title_template="P7（糖尿病合併早期腎病變）照護品質必要檢驗缺漏，建議安排相關檢驗",
            priority=RecommendationPriority.PRIORITY,
            trigger_grounded_in_spec=True,
            action_is_placeholder_content=False,
            spec_reference="P7 spec (d)",
            matcher=_nhi_ckd_p4p_lab_gap_matcher,
            guideline_id="Taiwan_NHI_CKD_P4P_2026",
            education_topic_code="RENAL_DIET_BASIC",
        ),
        # --- v2 新增 Tier B「僅資訊揭露」規則：alert_level="information"，
        # evidence_level 明確標註「本地驗證前僅供風險溝通」，文字不帶百分比
        # 門檻式建議（鐵律2）。---
        RecommendationRule(
            rule_id="WATCH_DM_INFO",
            title_template="WATCH-DM（未來HF風險）相關變數狀態（僅供風險溝通，非決策依據）",
            priority=RecommendationPriority.ROUTINE,
            trigger_grounded_in_spec=True,
            action_is_placeholder_content=True,
            spec_reference="OpenClaw for Diabetes HIS.md §6.3；§37 Local Validation",
            matcher=_watch_dm_info_matcher,
            alert_level="information",
            evidence_level="risk_communication_only_pending_local_validation",
        ),
        RecommendationRule(
            rule_id="PREVENT_INFO",
            title_template="AHA PREVENT（primary prevention ASCVD風險）相關變數狀態（僅供風險溝通，非決策依據）",
            priority=RecommendationPriority.ROUTINE,
            trigger_grounded_in_spec=True,
            action_is_placeholder_content=True,
            spec_reference="OpenClaw for Diabetes HIS.md §7；§37 Local Validation",
            matcher=_prevent_info_matcher,
            alert_level="information",
            evidence_level="risk_communication_only_pending_local_validation",
        ),
        RecommendationRule(
            rule_id="KARTER_INFO",
            title_template="Karter Hypoglycemia Risk Stratification 相關變數狀態（僅供風險溝通，非決策依據）",
            priority=RecommendationPriority.ROUTINE,
            trigger_grounded_in_spec=True,
            action_is_placeholder_content=True,
            spec_reference="OpenClaw for Diabetes HIS.md §9；§37 Local Validation",
            matcher=_karter_info_matcher,
            alert_level="information",
            evidence_level="risk_communication_only_pending_local_validation",
        ),
        RecommendationRule(
            rule_id="KFRE_INFO",
            title_template="4-variable KFRE（kidney failure風險）相關變數狀態（僅供風險溝通，非決策依據）",
            priority=RecommendationPriority.ROUTINE,
            trigger_grounded_in_spec=True,
            action_is_placeholder_content=True,
            spec_reference="OpenClaw for Diabetes HIS.md §12；§37 Local Validation",
            matcher=_kfre_info_matcher,
            alert_level="information",
            evidence_level="risk_communication_only_pending_local_validation",
        ),
    ]


class GuidelineRecommendationEngine:
    def __init__(self, rules: Sequence[RecommendationRule] | None = None):
        self.rules: list[RecommendationRule] = list(rules) if rules is not None else default_recommendation_rules()

    def build(self, input_data: GuidelineRecommendationInput) -> GuidelineRecommendationReport:
        recommendations: list[GuidelineRecommendation] = []
        warnings: list[str] = []

        for rule in self.rules:
            try:
                evidence = rule.matcher(input_data)
            except Exception as exc:  # matcher 拋例外時捕捉、記入 warnings、跳過該規則，不中斷整體流程
                warnings.append(f"規則 {rule.rule_id} 執行失敗，已略過：{exc!r}")
                continue
            if not evidence:
                continue
            rationale = "；".join(e.detail for e in evidence)
            recommendation_id = f"{rule.rule_id}::{input_data.patient_id}::{input_data.as_of_date.isoformat()}"

            related_finding_id: Optional[str] = None
            if rule.related_finding_id_matcher is not None:
                try:
                    related_finding_id = rule.related_finding_id_matcher(input_data)
                except Exception as exc:  # 同一規則的 related_finding_id_matcher 失敗不應阻斷該筆建議本身
                    warnings.append(f"規則 {rule.rule_id} 的 related_finding_id_matcher 執行失敗，已忽略：{exc!r}")

            recommendations.append(
                GuidelineRecommendation(
                    recommendation_id=recommendation_id,
                    rule_id=rule.rule_id,
                    title=rule.title_template,
                    rationale=rationale,
                    evidence=tuple(evidence),
                    priority=rule.priority,
                    trigger_grounded_in_spec=rule.trigger_grounded_in_spec,
                    action_is_placeholder_content=rule.action_is_placeholder_content,
                    education_topic_code=rule.education_topic_code,
                    guideline_id=rule.guideline_id,
                    recommendation_number=rule.recommendation_number,
                    evidence_level=rule.evidence_level,
                    applicable_population=rule.applicable_population,
                    exclusion=rule.exclusion,
                    alert_level=rule.alert_level,
                    related_finding_id=related_finding_id,
                )
            )

        return GuidelineRecommendationReport(
            patient_id=input_data.patient_id, as_of_date=input_data.as_of_date, recommendations=recommendations, warnings=warnings
        )
