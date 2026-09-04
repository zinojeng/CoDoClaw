"""
`pipeline.py` v2 端到端整合測試：組一份含新欄位的
`PatientEnrollmentState` → 跑完整條 v2 管線 → 斷言關鍵欄位（架構文件v2
第6節測試計畫 `test_pipeline_integration_v2.py`）。v1 既有端到端行為見
`tests/test_care_pipeline.py::test_pipeline_end_to_end_integration`。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from dm_eligibility.engine import EligibilityEngine
from dm_eligibility.models import (
    CKDAssessment,
    CodeClaim,
    DiagnosisRecord,
    Encounter,
    LabResult,
    MedicationOrder,
    PatientEnrollmentState,
    PhysicianStatus,
)

from dm_care_pipeline.calculators.base import CalculatorExecutionStatus, CalculatorTier
from dm_care_pipeline.calculators.registry import CalculatorRegistry
from dm_care_pipeline.clinical_data_layer import HypoglycemiaEventRecord
from dm_care_pipeline.clinical_data_object import ClinicalDomain
from dm_care_pipeline.data_integration import build_patient_clinical_profile
from dm_care_pipeline.followup import PendingOrder
from dm_care_pipeline.physician_decision import (
    PhysicianDecision,
    PhysicianDecisionStatus,
    Reviewable,
)
from dm_care_pipeline.pipeline import finalize_pipeline, run_stages_1_to_7

AS_OF = date(2024, 6, 1)

ALL_CALCULATOR_IDS = {
    "KDIGO_GA",
    "FIB4",
    "BNP_NTPROBNP_HF_SCREEN",
    "ABI_TBI_PAD_SCREEN",
    "IWGDF_FOOT_RISK",
    "ADA_HYPO_L1",
    "WATCH_DM",
    "PREVENT",
    "ASCVD_PCE_2013",
    "KARTER_HYPO_ED_HOSP",
    "KFRE_4VAR",
}


def dm_encounter(visit_date: date, icd10: str = "E11.21", with_med: bool = True) -> Encounter:
    return Encounter(
        encounter_id=f"E-{visit_date.isoformat()}-{icd10}",
        visit_date=visit_date,
        physician_id="DOC1",
        diagnoses=(DiagnosisRecord(icd10, is_primary=True),),
        medication_orders=(MedicationOrder("A10BA02"),) if with_med else (),
    )


def full_fixture_state() -> PatientEnrollmentState:
    return PatientEnrollmentState(
        patient_id="P1",
        as_of_date=AS_OF,
        encounters=[
            dm_encounter(AS_OF - timedelta(days=100), icd10="E11.21"),  # NEPHROPATHY
            dm_encounter(AS_OF, icd10="E11.21"),
        ],
        lab_results=[
            LabResult("09006C", AS_OF - timedelta(days=10), value=9.5),
            LabResult("09005C", AS_OF - timedelta(days=10), value=180.0),
            LabResult("09001C", AS_OF - timedelta(days=10), value=220.0),
            LabResult("09043C", AS_OF - timedelta(days=10), value=40.0),
        ],
        claims=[CodeClaim("P1407C", AS_OF - timedelta(days=100))],
        ckd_assessments=[CKDAssessment(AS_OF - timedelta(days=10), egfr=50.0, upcr=200.0, uacr=45.0, is_diabetic=True)],
        vpn_other_institution_enrolled=False,
        age_years=55,
    )


class _FakeOrderSource:
    def __init__(self, orders):
        self._orders = orders

    def get_pending_orders(self, patient_id, as_of):
        return self._orders


def _run():
    state = full_fixture_state()
    physician = PhysicianStatus(physician_id="DOC1", is_dm_ckd_dual_qualified=True)
    eligibility_report = EligibilityEngine().evaluate(state, physician)
    return run_stages_1_to_7(state, eligibility_report=eligibility_report, physician=physician)


# ---------------------------------------------------------------------------
# calculator_results
# ---------------------------------------------------------------------------


def test_all_default_calculators_are_computed():
    run_result = _run()
    assert set(run_result.calculator_results.keys()) == ALL_CALCULATOR_IDS


def test_karter_inputs_do_not_crash_on_leap_day_as_of_date():
    """回歸測試（Codex 審閱發現的真實 bug）：`_build_karter_inputs()` 原本
    用 `date(as_of.year - 1, month, day)` 手動減一年推算12個月回溯窗口起點；
    若 `as_of_date` 恰為閏年2/29且前一年非閏年，會直接 ValueError 崩潰。
    已改用 `timedelta(days=365)`。直接測 `_build_karter_inputs()`（而非透過
    `run_stages_1_to_7()`，雖然後者現在也接受 `encounter_utilization`
    參數）——直接建構 profile 更精確地聚焦在原本會崩潰的分支本身，見
    `test_layer1_data_actually_reaches_calculators_via_run_stages_1_to_7()`
    驗證 `run_stages_1_to_7()` 端到端的 Layer1 透傳。"""
    from dm_care_pipeline.clinical_data_layer import EncounterUtilizationRecord
    from dm_care_pipeline.pipeline import _build_karter_inputs

    state = PatientEnrollmentState(patient_id="P1", as_of_date=date(2024, 2, 29), encounters=[])
    profile = build_patient_clinical_profile(
        state,
        encounter_utilization=(
            EncounterUtilizationRecord(encounter_id="E1", visit_date=date(2024, 1, 1), setting="ed", hypoglycemia_related=True),
        ),
    )
    inputs = _build_karter_inputs(profile, kdigo_ga_result=None)
    assert inputs.ed_visits_prior_12mo == 1
    assert inputs.prior_hypo_related_ed_or_hosp is True


def test_future_dated_ckd_assessment_is_ignored_by_calculator_input_builders():
    """回歸測試（Codex 審閱發現的真實 bug）：`_latest_ckd_assessment()` 等
    `pipeline.py` 的 `_latest_*` helper 原本未過濾「日期不可晚於
    as_of_date」，未來日期的紀錄會被誤判為『最新』並餵給 calculator。"""
    from dm_care_pipeline.pipeline import _build_ckd_ga_inputs
    from dm_care_pipeline.complication_identification import identify_complications

    state = PatientEnrollmentState(
        patient_id="P1",
        as_of_date=AS_OF,
        encounters=[],
        ckd_assessments=[
            CKDAssessment(AS_OF - timedelta(days=10), egfr=50.0, uacr=45.0, is_diabetic=True),  # 合法：過去
            CKDAssessment(AS_OF + timedelta(days=10), egfr=95.0, uacr=5.0, is_diabetic=True),  # 未來日期，應被忽略
        ],
    )
    profile = build_patient_clinical_profile(state)
    complication_report = identify_complications(profile)
    inputs = _build_ckd_ga_inputs(profile, complication_report)
    assert inputs.egfr == 50.0  # 而非未來那筆的 95.0


def test_tier_a_calculators_can_reach_computed_status():
    run_result = _run()
    ckd = run_result.calculator_results["KDIGO_GA"]
    assert ckd.execution_status == CalculatorExecutionStatus.COMPUTED
    assert ckd.result_values["g_stage"] == "G3a"
    assert ckd.result_values["a_stage"] == "A2"


def test_tier_b_calculators_never_fabricate_a_value():
    run_result = _run()
    for calc_id in ("WATCH_DM", "PREVENT", "ASCVD_PCE_2013", "KARTER_HYPO_ED_HOSP", "KFRE_4VAR"):
        result = run_result.calculator_results[calc_id]
        assert result.tier == CalculatorTier.B
        assert result.execution_status in (
            CalculatorExecutionStatus.REQUIRES_EXTERNAL_VALIDATED_MODEL,
            CalculatorExecutionStatus.NOT_APPLICABLE,
        )
        assert result.result_values is None


def test_pipeline_wires_sex_into_prevent_and_pce_builders():
    """回歸測試（Codex #20）：pipeline.py 的 _build_prevent_inputs()/
    _build_legacy_ascvd_inputs() 先前完全不傳 sex，即使 profile.sex 有
    提供也整條丟棄。"""
    state = full_fixture_state()
    physician = PhysicianStatus(physician_id="DOC1", is_dm_ckd_dual_qualified=True)
    eligibility_report = EligibilityEngine().evaluate(state, physician)
    run_result = run_stages_1_to_7(state, eligibility_report=eligibility_report, physician=physician, sex="female")

    for calc_id in ("PREVENT", "ASCVD_PCE_2013"):
        sex_field = next(f for f in run_result.calculator_results[calc_id].inputs if f.name == "sex")
        assert sex_field.provided is True
        assert sex_field.value == "female"


def test_pipeline_routes_mi_history_patient_to_secondary_prevention():
    """回歸測試（Codex #21）：只有 I21（急性心肌梗塞）診斷、沒有 I25.x
    （慢性缺血性心臟病）的病人，先前不會被 already_in_secondary_
    prevention() 判定為 secondary prevention，PREVENT/PCE 停留在 primary
    prevention 分支而非規格要求的 established ASCVD pathway。"""
    state = full_fixture_state()
    state.encounters.append(dm_encounter(AS_OF, icd10="I21.9"))
    physician = PhysicianStatus(physician_id="DOC1", is_dm_ckd_dual_qualified=True)
    eligibility_report = EligibilityEngine().evaluate(state, physician)
    run_result = run_stages_1_to_7(state, eligibility_report=eligibility_report, physician=physician)

    for calc_id in ("PREVENT", "ASCVD_PCE_2013"):
        assert run_result.calculator_results[calc_id].execution_status == CalculatorExecutionStatus.NOT_APPLICABLE


def test_pipeline_ada_hypo_insufficient_data_for_patient_with_no_encounters():
    """回歸測試（Codex #14）：完全沒有就診/用藥紀錄時，
    _active_drug_classes() 回傳空集合，先前一律被當成「確認未使用胰島素/
    SU/meglitinide」（False），讓完全沒有資料的病人被判定 COMPUTED LOW，
    而非 calculator 本身設計的 INSUFFICIENT_DATA。"""
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, age_years=55)
    physician = PhysicianStatus(physician_id="DOC1", is_dm_ckd_dual_qualified=True)
    eligibility_report = EligibilityEngine().evaluate(state, physician)
    run_result = run_stages_1_to_7(state, eligibility_report=eligibility_report, physician=physician)

    ada_result = run_result.calculator_results["ADA_HYPO_L1"]
    assert ada_result.execution_status == CalculatorExecutionStatus.INSUFFICIENT_DATA
    assert set(ada_result.missing_inputs) == {"on_insulin", "on_sulfonylurea", "on_meglitinide"}


def test_pipeline_ada_hypo_recent_severe_hypoglycemia_drives_high_risk():
    """回歸測試（Codex #15）：profile.hypoglycemia_events 先前完全沒有
    消費者，即使有近期 Level 2/3 低血糖事件也不影響風險分級。"""
    state = full_fixture_state()
    state.encounters.append(
        Encounter(
            encounter_id="E-insulin",
            visit_date=AS_OF,
            physician_id="DOC1",
            diagnoses=(DiagnosisRecord("E11.9", is_primary=True),),
            medication_orders=(MedicationOrder("A10AB01"),),
        )
    )
    physician = PhysicianStatus(physician_id="DOC1", is_dm_ckd_dual_qualified=True)
    eligibility_report = EligibilityEngine().evaluate(state, physician)
    hypo_events = (HypoglycemiaEventRecord(event_date=AS_OF - timedelta(days=30), severity="level2", setting="ed"),)
    run_result = run_stages_1_to_7(
        state, eligibility_report=eligibility_report, physician=physician, hypoglycemia_events=hypo_events
    )

    ada_result = run_result.calculator_results["ADA_HYPO_L1"]
    assert ada_result.execution_status == CalculatorExecutionStatus.COMPUTED
    assert ada_result.result_values["risk_level"] == "HIGH"
    assert "recent_level2_or_3_hypoglycemia_3_6mo" in ada_result.result_values["matched_major_factors"]


def test_pipeline_ada_hypo_no_hypo_event_data_stays_insufficient_data():
    """正向對照：沒有任何 hypoglycemia_events 資料時（既不是空事件史，
    是完全沒有這個資料來源），不可假裝已完整評估 major/minor factors——
    risk_factors_assessed 應維持 False，calculator 回傳 INSUFFICIENT_DATA
    而非冒充 LOW/MODERATE。"""
    state = full_fixture_state()
    state.encounters.append(
        Encounter(
            encounter_id="E-insulin-2",
            visit_date=AS_OF,
            physician_id="DOC1",
            diagnoses=(DiagnosisRecord("E11.9", is_primary=True),),
            medication_orders=(MedicationOrder("A10AB01"),),
        )
    )
    physician = PhysicianStatus(physician_id="DOC1", is_dm_ckd_dual_qualified=True)
    eligibility_report = EligibilityEngine().evaluate(state, physician)
    run_result = run_stages_1_to_7(state, eligibility_report=eligibility_report, physician=physician)

    ada_result = run_result.calculator_results["ADA_HYPO_L1"]
    assert ada_result.execution_status == CalculatorExecutionStatus.INSUFFICIENT_DATA
    assert ada_result.missing_inputs == ("risk_factors_assessed",)


def test_pipeline_kfre_not_applicable_for_non_ckd_patient():
    """回歸測試（Codex #22）：G1A1（明確非 CKD）的病人先前也會拿到 KFRE
    的「待驗證模型」care gap，即使 KFRE 定義即為 CKD 病人專用。"""
    state = PatientEnrollmentState(
        patient_id="P1",
        as_of_date=AS_OF,
        encounters=[dm_encounter(AS_OF, icd10="E11.9")],
        ckd_assessments=[CKDAssessment(AS_OF, egfr=95.0, upcr=10.0, uacr=10.0, is_diabetic=True)],
        age_years=55,
    )
    physician = PhysicianStatus(physician_id="DOC1", is_dm_ckd_dual_qualified=True)
    eligibility_report = EligibilityEngine().evaluate(state, physician)
    run_result = run_stages_1_to_7(state, eligibility_report=eligibility_report, physician=physician)

    assert run_result.calculator_results["KFRE_4VAR"].execution_status == CalculatorExecutionStatus.NOT_APPLICABLE


def test_custom_calculator_registry_is_not_auto_populated_with_defaults():
    """呼叫端顯式傳入自訂（空）registry 時，不應被本檔案偷偷塞入預設11個
    calculator——尊重呼叫端的顯式選擇。"""
    state = full_fixture_state()
    physician = PhysicianStatus(physician_id="DOC1", is_dm_ckd_dual_qualified=True)
    eligibility_report = EligibilityEngine().evaluate(state, physician)
    empty_registry = CalculatorRegistry()
    run_result = run_stages_1_to_7(
        state, eligibility_report=eligibility_report, physician=physician, calculator_registry=empty_registry
    )
    assert run_result.calculator_results == {}


# ---------------------------------------------------------------------------
# clinical_state
# ---------------------------------------------------------------------------


def test_clinical_state_has_confirmed_kidney_finding():
    run_result = _run()
    kidney_findings = run_result.clinical_state.by_domain(ClinicalDomain.KIDNEY)
    assert any(f.status.value == "confirmed" for f in kidney_findings)


def test_clinical_state_domain_summaries_cover_every_domain():
    run_result = _run()
    assert set(run_result.clinical_state.domain_summaries.keys()) == set(ClinicalDomain)


# ---------------------------------------------------------------------------
# guideline_report / medication_report / decision_record
# ---------------------------------------------------------------------------


def test_decision_record_merges_guideline_and_medication_recommendations():
    run_result = _run()
    total = len(run_result.guideline_report.recommendations) + len(run_result.medication_report.recommendations)
    assert run_result.decision_record.pending_count() == total
    assert total > 0


def test_all_presented_recommendations_satisfy_reviewable():
    run_result = _run()
    for rec in run_result.decision_record.presented_recommendations:
        assert isinstance(rec, Reviewable)


def test_medication_report_kidney_gap_triggers_for_ckd_without_protective_drug():
    run_result = _run()
    assert any(r.rule_id == "KIDNEY_PROTECTIVE_THERAPY_GAP" for r in run_result.medication_report.recommendations)


# ---------------------------------------------------------------------------
# care_gap_agent_report
# ---------------------------------------------------------------------------


def test_care_gap_agent_report_has_three_clocks():
    run_result = _run()
    report = run_result.care_gap_agent_report
    assert len(report.clinical_clock) > 0
    assert len(report.patient_specific_clock) > 0
    # P4P clock 依 codes_in_scope 而定，本 fixture 有 P1407C claim


# ---------------------------------------------------------------------------
# pre_visit_brief
# ---------------------------------------------------------------------------


def test_pre_visit_brief_assembled_from_same_run():
    run_result = _run()
    brief = run_result.pre_visit_brief
    assert brief.patient_id == "P1"
    assert brief.as_of_date == AS_OF
    assert "HBA1C" in brief.today_widget
    assert len(brief.complication_map) == len(run_result.clinical_state.domain_summaries)
    assert set(brief.evidence_index.keys()) == {f.finding_id for f in run_result.clinical_state.findings}
    assert brief.advanced_risk_widget == tuple(run_result.calculator_results.values())


def test_pre_visit_brief_guideline_gap_widget_excludes_medication_recommendations():
    run_result = _run()
    from dm_care_pipeline.guideline_recommendation import GuidelineRecommendation

    for rec, _decision in run_result.pre_visit_brief.guideline_gap_widget:
        assert isinstance(rec, GuidelineRecommendation)


def test_pre_visit_brief_alert_report_derived_from_clinical_state_findings():
    run_result = _run()
    assert run_result.pre_visit_brief.alert_report.patient_id == "P1"


def test_alert_config_is_actually_applied_not_silently_ignored():
    """回歸測試（Codex 審閱發現的真實 bug）：`run_stages_1_to_7()` 原本
    接受 `alert_config` 參數卻從未傳到 `generate_pre_visit_brief()`，呼叫端
    自訂的 alert 分級策略會被靜默忽略。"""
    from dm_care_pipeline.alert import AlertClassificationConfig, AlertLevel
    from dm_care_pipeline.clinical_data_object import ClinicalStatus

    state = full_fixture_state()
    physician = PhysicianStatus(physician_id="DOC1", is_dm_ckd_dual_qualified=True)
    eligibility_report = EligibilityEngine().evaluate(state, physician)

    # 自訂策略：把 CONFIRMED 也升級為 clinical_attention（預設只有
    # HIGH_RISK/CARE_GAP）——若 alert_config 真的有作用，NEPHROPATHY
    # CONFIRMED finding 應從預設的 INFORMATION 變成 CLINICAL_ATTENTION。
    custom_cfg = AlertClassificationConfig(clinical_attention_status=frozenset({ClinicalStatus.CONFIRMED}))
    run_result = run_stages_1_to_7(
        state, eligibility_report=eligibility_report, physician=physician, alert_config=custom_cfg
    )
    confirmed_findings = [f for f in run_result.clinical_state.findings if f.status == ClinicalStatus.CONFIRMED]
    assert confirmed_findings  # fixture 有 NEPHROPATHY CONFIRMED
    for f in confirmed_findings:
        assert f in run_result.pre_visit_brief.alert_report.by_level[AlertLevel.CLINICAL_ATTENTION]


# ---------------------------------------------------------------------------
# finalize_pipeline()
# ---------------------------------------------------------------------------


def test_finalize_pipeline_applies_education_config_consistently():
    """回歸測試（Codex 審閱發現的真實 bug）：`education_config` 先前只
    套用在 `education_plan.topics`，`education_report.resource_topics`
    卻用預設設定重算，兩者結果會不一致。改用自訂（空）
    `EducationTopicMappingConfig` 驗證兩處輸出的 topic_code 集合一致。"""
    from dm_care_pipeline.education import EducationTopicMappingConfig

    run_result = _run()
    for rec in run_result.decision_record.presented_recommendations:
        run_result.decision_record.record_decision(
            PhysicianDecision(recommendation_id=rec.recommendation_id, status=PhysicianDecisionStatus.ACCEPTED, physician_id="DOC1")
        )
    # complication_to_topics 清空後，topic 只會來自已核可建議的
    # education_topic_code；resources_by_topic 也清空，讓每個 topic 的
    # resources 皆為空（可觀察到的差異：若未套用自訂設定，預設
    # resources_by_topic 會給 FOOT_CARE_BASIC/RENAL_DIET_BASIC/
    # GLYCEMIC_CONTROL_BASIC 非空的 placeholder 資源）。
    custom_cfg = EducationTopicMappingConfig(complication_to_topics={}, resources_by_topic={})
    final_result = finalize_pipeline(run_result, education_config=custom_cfg)
    plan_topics = {t.topic_code for t in final_result.education_plan.topics}
    report_topics = {t.topic_code for t in final_result.education_report.resource_topics}
    assert plan_topics == report_topics  # 兩處應套用同一份自訂設定，結果一致
    assert plan_topics  # 至少有一筆（來自已核可建議的 education_topic_code）
    plan_resources = {t.topic_code: t.resources for t in final_result.education_plan.topics}
    report_resources = {t.topic_code: t.resources for t in final_result.education_report.resource_topics}
    assert plan_resources == report_resources
    assert all(resources == () for resources in plan_resources.values())  # resources_by_topic 清空後應皆無資源


def test_finalize_pipeline_produces_education_report_and_order_tracking():
    run_result = _run()
    for rec in run_result.decision_record.presented_recommendations:
        run_result.decision_record.record_decision(
            PhysicianDecision(recommendation_id=rec.recommendation_id, status=PhysicianDecisionStatus.ACCEPTED, physician_id="DOC1")
        )
    final_result = finalize_pipeline(run_result)
    assert final_result.education_report.patient_id == "P1"
    assert final_result.order_tracking_report.patient_id == "P1"
    assert any("未串接" in w for w in final_result.order_tracking_report.warnings)  # 未提供 order_source


def test_finalize_pipeline_rejects_mismatched_decision_record():
    """回歸測試（Codex 審閱發現的真實 bug）：`finalize_pipeline()` 原本
    未驗證顯式傳入的 `decision_record` 與 `run_result.profile` 屬於同一位
    病人/同一次評估——若呼叫端傳錯（跨病人），會被直接混用產生衛教/
    追蹤計畫，形成跨病人資料汙染且無任何錯誤提示。"""
    from dm_care_pipeline.physician_decision import DecisionValidationError, PhysicianDecisionRecord

    run_result = _run()
    other_patient_decision_record = PhysicianDecisionRecord(
        patient_id="SOME_OTHER_PATIENT", as_of_date=AS_OF, presented_recommendations=()
    )
    with pytest.raises(DecisionValidationError):
        finalize_pipeline(run_result, other_patient_decision_record)


def test_finalize_pipeline_with_order_source_populates_pending_orders():
    run_result = _run()
    for rec in run_result.decision_record.presented_recommendations:
        run_result.decision_record.record_decision(
            PhysicianDecision(recommendation_id=rec.recommendation_id, status=PhysicianDecisionStatus.ACCEPTED, physician_id="DOC1")
        )
    order = PendingOrder(order_id="O1", order_type="FIBROSCAN", ordered_date=AS_OF, status="ORDERED")
    final_result = finalize_pipeline(run_result, order_source=_FakeOrderSource((order,)))
    assert final_result.order_tracking_report.pending_orders == (order,)
    assert any("FIBROSCAN" in a for a in final_result.education_report.today_actions)


def test_backward_compatible_v1_style_call_still_works():
    """v1 呼叫端零改動即可運作：不傳任何 v2 新參數。"""
    run_result = _run()
    final_result = finalize_pipeline(run_result)
    assert final_result.followup_plan.next_recommended_visit_date >= AS_OF
    assert isinstance(final_result.education_plan.topics, list)


def test_layer1_data_actually_reaches_calculators_via_run_stages_1_to_7():
    """回歸測試（Codex 審閱發現的真實 bug）：`run_stages_1_to_7()` 原本
    完全不接受 clinical_data_layer.py 型別的參數，導致呼叫端無論如何都
    無法讓 ABI/TBI、足部檢查等 calculator 真正吃到 Layer1 擴充資料——
    v2 calculator/CDS 流程恆看到空預設值。此測試直接以 vascular_exams
    驅動 ABI_TBI_PAD_SCREEN 從 INSUFFICIENT_DATA 變成真正 COMPUTED。"""
    from dm_care_pipeline.calculators.base import CalculatorExecutionStatus
    from dm_care_pipeline.clinical_data_layer import VascularExam

    state = full_fixture_state()
    physician = PhysicianStatus(physician_id="DOC1", is_dm_ckd_dual_qualified=True)
    eligibility_report = EligibilityEngine().evaluate(state, physician)

    # 不傳 vascular_exams：ABI/TBI 應為 INSUFFICIENT_DATA
    baseline = run_stages_1_to_7(state, eligibility_report=eligibility_report, physician=physician)
    assert baseline.calculator_results["ABI_TBI_PAD_SCREEN"].execution_status == CalculatorExecutionStatus.INSUFFICIENT_DATA

    # 傳入 vascular_exams：ABI/TBI 應真正算出結果
    with_layer1 = run_stages_1_to_7(
        state,
        eligibility_report=eligibility_report,
        physician=physician,
        vascular_exams=(VascularExam(exam_date=AS_OF, abi_right=0.5, abi_left=1.0),),
    )
    abi_result = with_layer1.calculator_results["ABI_TBI_PAD_SCREEN"]
    assert abi_result.execution_status == CalculatorExecutionStatus.COMPUTED
    assert abi_result.result_values["abi_right"] == 0.5
