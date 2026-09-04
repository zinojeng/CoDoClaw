"""
`care_gap_clocks.py`（Care-Gap Agent：Clinical/P4P/Patient-Specific 三時鐘
+ Advanced Screening）測試。
"""

from __future__ import annotations

from datetime import date, timedelta

from dm_eligibility.models import DiagnosisRecord, Encounter, LabResult, PatientEnrollmentState

from dm_care_pipeline.calculators.base import CalculatorExecutionStatus, CalculatorResult, CalculatorTier
from dm_care_pipeline.calculators.fib4 import FIB4Calculator, FIB4Inputs
from dm_care_pipeline.calculators.iwgdf_foot import IWGDF_FOLLOWUP_INTERVAL_DAYS
from dm_care_pipeline.care_gap import assess_care_gaps
from dm_care_pipeline.care_gap_clocks import (
    CLINICAL_CLOCK_REGISTRY,
    CKDMonitoringFrequencyClockRule,
    ClockEvaluation,
    IWGDFFootClockRule,
    RetinopathySeverityClockRule,
    advanced_screening_gap,
    assess_care_gap_agent,
    clinical_clock_view,
    p4p_clock_view,
    to_finding,
)
from dm_care_pipeline.clinical_data_layer import FootNeuroExam, ProcedureRecord, VitalSignObservation
from dm_care_pipeline.clinical_data_object import ClinicalDomain, ClinicalStatus
from dm_care_pipeline.clinical_state import derive_clinical_state
from dm_care_pipeline.complication_identification import identify_complications
from dm_care_pipeline.data_integration import build_patient_clinical_profile
from dm_care_pipeline.risk import assess_risk
from dm_care_pipeline.trend_analysis import analyze_clinical_trends

AS_OF = date(2024, 6, 1)


def dm_encounter(visit_date: date, icd10: str = "E11.21") -> Encounter:
    return Encounter(
        encounter_id=f"E-{visit_date.isoformat()}-{icd10}",
        visit_date=visit_date,
        physician_id="DOC1",
        diagnoses=(DiagnosisRecord(icd10, is_primary=True),),
    )


def build_reports(state: PatientEnrollmentState, **profile_kwargs):
    profile = build_patient_clinical_profile(state, **profile_kwargs)
    trend_report = analyze_clinical_trends(profile)
    complication_report = identify_complications(profile)
    care_gap_report = assess_care_gaps(profile, codes_in_scope=["P1407C"], include_quality_monitoring=False)
    risk_result = assess_risk(profile, trend_report, complication_report)
    clinical_state = derive_clinical_state(profile, complication_report, care_gap_report, risk_result)
    return profile, care_gap_report, clinical_state


# ---------------------------------------------------------------------------
# ClockEvaluation / to_finding()
# ---------------------------------------------------------------------------


def _make_eval(**overrides) -> ClockEvaluation:
    defaults = dict(
        item_code="X",
        description="desc",
        clock_type="CLINICAL",
        last_performed_date=None,
        interval_days_range=(365, 365),
        next_due_earliest=None,
        next_due_latest=None,
        satisfied=False,
        is_placeholder_interval=True,
    )
    defaults.update(overrides)
    return ClockEvaluation(**defaults)


def test_to_finding_returns_none_when_satisfied():
    ev = _make_eval(satisfied=True)
    assert to_finding(ev, "P1", ClinicalDomain.EYE, AS_OF) is None


def test_to_finding_returns_care_gap_finding_when_not_satisfied():
    ev = _make_eval(satisfied=False, description="眼底檢查逾期")
    finding = to_finding(ev, "P1", ClinicalDomain.EYE, AS_OF)
    assert finding is not None
    assert finding.status == ClinicalStatus.CARE_GAP
    assert finding.domain == ClinicalDomain.EYE
    assert finding.condition == "眼底檢查逾期"


def test_to_finding_is_placeholder_matches_evaluation():
    ev = _make_eval(satisfied=False, is_placeholder_interval=True)
    finding = to_finding(ev, "P1", ClinicalDomain.EYE, AS_OF)
    assert finding.is_placeholder is True


def test_to_finding_deterministic_across_repeated_calls():
    ev = _make_eval(satisfied=False, item_code="EYE_EXAM")
    f1 = to_finding(ev, "P1", ClinicalDomain.EYE, AS_OF)
    f2 = to_finding(ev, "P1", ClinicalDomain.EYE, AS_OF)
    assert f1.finding_id == f2.finding_id  # 純函式：相同輸入應產生相同 id


# ---------------------------------------------------------------------------
# Clinical Clock
# ---------------------------------------------------------------------------


def test_clinical_clock_registry_covers_nine_items():
    assert set(CLINICAL_CLOCK_REGISTRY.keys()) == {
        "HBA1C",
        "LIPID_PANEL",
        "EGFR",
        "UACR",
        "EYE_EXAM",
        "FOOT_EXAM",
        "BLOOD_PRESSURE",
        "WEIGHT",
        "SMOKING_STATUS",
    }


def test_clinical_clock_is_always_placeholder_interval():
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, encounters=[])
    profile = build_patient_clinical_profile(state)
    evaluations = clinical_clock_view(profile, AS_OF)
    assert all(ev.is_placeholder_interval for ev in evaluations)


def test_lab_based_clinical_clock_satisfied_within_annual_window():
    state = PatientEnrollmentState(
        patient_id="P1",
        as_of_date=AS_OF,
        encounters=[],
        lab_results=[LabResult("09006C", AS_OF - timedelta(days=100), value=7.0)],
    )
    profile = build_patient_clinical_profile(state)
    evaluations = {ev.item_code: ev for ev in clinical_clock_view(profile, AS_OF)}
    hba1c = evaluations["CLINICAL_CLOCK:HBA1C"]
    assert hba1c.satisfied is True
    assert hba1c.last_performed_date == AS_OF - timedelta(days=100)


def test_lab_based_clinical_clock_overdue_beyond_annual_window():
    state = PatientEnrollmentState(
        patient_id="P1",
        as_of_date=AS_OF,
        encounters=[],
        lab_results=[LabResult("09006C", AS_OF - timedelta(days=400), value=7.0)],
    )
    profile = build_patient_clinical_profile(state)
    evaluations = {ev.item_code: ev for ev in clinical_clock_view(profile, AS_OF)}
    assert evaluations["CLINICAL_CLOCK:HBA1C"].satisfied is False


def test_lab_based_clinical_clock_no_data_is_unsatisfied_with_no_last_performed():
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, encounters=[], lab_results=[])
    profile = build_patient_clinical_profile(state)
    evaluations = {ev.item_code: ev for ev in clinical_clock_view(profile, AS_OF)}
    eye = evaluations["CLINICAL_CLOCK:EYE_EXAM"]
    assert eye.satisfied is False
    assert eye.last_performed_date is None


def test_data_layer_based_clinical_clock_uses_foot_neuro_exam():
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, encounters=[])
    profile = build_patient_clinical_profile(
        state, foot_neuro_exams=(FootNeuroExam(exam_date=AS_OF - timedelta(days=30)),)
    )
    evaluations = {ev.item_code: ev for ev in clinical_clock_view(profile, AS_OF)}
    foot = evaluations["CLINICAL_CLOCK:FOOT_EXAM"]
    assert foot.satisfied is True
    assert foot.last_performed_date == AS_OF - timedelta(days=30)


def test_data_layer_based_clinical_clock_uses_vital_sign_for_blood_pressure_and_weight():
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, encounters=[])
    profile = build_patient_clinical_profile(
        state,
        vital_signs=(VitalSignObservation(observation_date=AS_OF - timedelta(days=10), systolic_bp=130, weight_kg=70),),
    )
    evaluations = {ev.item_code: ev for ev in clinical_clock_view(profile, AS_OF)}
    assert evaluations["CLINICAL_CLOCK:BLOOD_PRESSURE"].satisfied is True
    assert evaluations["CLINICAL_CLOCK:WEIGHT"].satisfied is True
    assert evaluations["CLINICAL_CLOCK:SMOKING_STATUS"].satisfied is False  # smoking_status 未提供


# ---------------------------------------------------------------------------
# P4P Clock
# ---------------------------------------------------------------------------


def test_p4p_clock_view_wraps_care_gap_report_without_recomputation():
    state = PatientEnrollmentState(
        patient_id="P1",
        as_of_date=AS_OF,
        encounters=[],
        lab_results=[LabResult("09006C", AS_OF - timedelta(days=10), value=7.0)],
    )
    profile = build_patient_clinical_profile(state)
    care_gap_report = assess_care_gaps(profile, codes_in_scope=["P1407C"], include_quality_monitoring=False)
    evaluations = p4p_clock_view(care_gap_report)
    assert len(evaluations) == sum(len(v) for v in care_gap_report.by_code.values())
    assert all(ev.is_placeholder_interval is False for ev in evaluations)
    assert all(ev.clock_type == "P4P" for ev in evaluations)
    # min==max（單一值窗口）
    assert all(ev.interval_days_range[0] == ev.interval_days_range[1] for ev in evaluations)
    hba1c_eval = next(ev for ev in evaluations if "09006C" in ev.item_code)
    assert hba1c_eval.satisfied is True


def test_p4p_clock_view_unsatisfied_item_has_no_last_performed():
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, encounters=[], lab_results=[])
    profile = build_patient_clinical_profile(state)
    care_gap_report = assess_care_gaps(profile, codes_in_scope=["P1407C"], include_quality_monitoring=False)
    evaluations = p4p_clock_view(care_gap_report)
    assert all(ev.satisfied is False for ev in evaluations)
    assert all(ev.last_performed_date is None for ev in evaluations)


# ---------------------------------------------------------------------------
# Patient-Specific Clock
# ---------------------------------------------------------------------------


def test_iwgdf_foot_clock_rule_degrades_to_annual_placeholder_without_iwgdf_finding():
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, encounters=[])
    profile, _, clinical_state = build_reports(state)
    ev = IWGDFFootClockRule().evaluate(profile, clinical_state)
    assert ev is not None
    assert ev.is_placeholder_interval is True
    assert ev.interval_days_range == (365, 365)


def test_iwgdf_foot_clock_rule_uses_real_interval_when_category_available():
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, encounters=[])
    profile, care_gap_report, _ = build_reports(state)
    trend_report = analyze_clinical_trends(profile)
    complication_report = identify_complications(profile)
    risk_result = assess_risk(profile, trend_report, complication_report)

    iwgdf_result = CalculatorResult(
        calculator_id="IWGDF_FOOT_RISK",
        calculator_version="v1.0",
        tier=CalculatorTier.A,
        patient_id="P1",
        computed_at=AS_OF,
        execution_status=CalculatorExecutionStatus.COMPUTED,
        result_values={"category": 2, "overdue": False},
        result_summary="IWGDF Foot Risk = 2",
        clinical_status=ClinicalStatus.HIGH_RISK,
    )
    clinical_state = derive_clinical_state(
        profile, complication_report, care_gap_report, risk_result, calculator_results={"IWGDF_FOOT_RISK": iwgdf_result}
    )
    ev = IWGDFFootClockRule().evaluate(profile, clinical_state)
    assert ev.is_placeholder_interval is False
    assert ev.interval_days_range == IWGDF_FOLLOWUP_INTERVAL_DAYS[2]


def test_retinopathy_and_ckd_clock_rules_always_placeholder():
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, encounters=[])
    profile, _, clinical_state = build_reports(state)
    retinopathy_ev = RetinopathySeverityClockRule().evaluate(profile, clinical_state)
    ckd_ev = CKDMonitoringFrequencyClockRule().evaluate(profile, clinical_state)
    assert retinopathy_ev.is_placeholder_interval is True
    assert ckd_ev.is_placeholder_interval is True


def test_retinopathy_clock_rule_ignores_future_dated_ophthalmology_finding():
    """回歸測試（Codex 審閱發現的真實 bug）：`RetinopathySeverityClockRule`
    原本未過濾未來日期，會把尚未發生的眼科檢查當成「已完成」，讓 satisfied
    誤判為 True。"""
    from dm_care_pipeline.clinical_data_layer import OphthalmologyFinding

    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, encounters=[])
    profile, _, clinical_state = build_reports(
        state,
        ophthalmology_findings=(
            OphthalmologyFinding(
                exam_date=AS_OF + timedelta(days=30), method="manual", dr_classification="none"
            ),
        ),
    )
    ev = RetinopathySeverityClockRule().evaluate(profile, clinical_state)
    assert ev.last_performed_date is None
    assert ev.satisfied is False


# ---------------------------------------------------------------------------
# Advanced Screening
# ---------------------------------------------------------------------------


def _synthetic_computed_watch_dm(clinical_status: ClinicalStatus) -> CalculatorResult:
    # ★ 測試用途：真正的 WatchDmCalculator 是 Tier B，execution_status 恆為
    # REQUIRES_EXTERNAL_VALIDATED_MODEL（CalculatorResult.__post_init__
    # 強制禁止 Tier B 落入 COMPUTED，鐵律2）。這裡以 tier=A 構造一個假想的
    # 「未來已驗證 WATCH-DM 實作」結果，單獨測試 advanced_screening_gap()
    # 的判斷分支邏輯，不代表目前系統真的能產生這種結果。
    return CalculatorResult(
        calculator_id="WATCH_DM",
        calculator_version="v1.0",
        tier=CalculatorTier.A,
        patient_id="P1",
        computed_at=AS_OF,
        execution_status=CalculatorExecutionStatus.COMPUTED,
        result_values={"risk": "high"},
        clinical_status=clinical_status,
    )


def test_advanced_screening_watch_dm_tier_b_never_triggers():
    from dm_care_pipeline.calculators.tier_b.watch_dm import WatchDmCalculator, WatchDmInputs

    real_watch_dm = WatchDmCalculator().compute(WatchDmInputs(patient_id="P1", as_of=AS_OF))
    findings = advanced_screening_gap(real_watch_dm, None, bnp_ordered=False, vcte_ordered=False, patient_id="P1", as_of=AS_OF)
    assert findings == []


def test_advanced_screening_watch_dm_high_risk_triggers_when_not_ordered():
    watch_dm = _synthetic_computed_watch_dm(ClinicalStatus.HIGH_RISK)
    findings = advanced_screening_gap(watch_dm, None, bnp_ordered=False, vcte_ordered=False, patient_id="P1", as_of=AS_OF)
    assert len(findings) == 1
    assert findings[0].domain == ClinicalDomain.HEART_FAILURE
    assert findings[0].status == ClinicalStatus.CARE_GAP


def test_advanced_screening_watch_dm_high_risk_suppressed_when_already_ordered():
    watch_dm = _synthetic_computed_watch_dm(ClinicalStatus.HIGH_RISK)
    findings = advanced_screening_gap(watch_dm, None, bnp_ordered=True, vcte_ordered=False, patient_id="P1", as_of=AS_OF)
    assert findings == []


def test_advanced_screening_fib4_elevated_triggers_vcte_suggestion():
    fib4 = FIB4Calculator().compute(
        FIB4Inputs(patient_id="P1", as_of=AS_OF, age_years=50, ast_u_l=100, alt_u_l=20, platelet_10e9_l=100)
    )
    assert fib4.clinical_status == ClinicalStatus.SUSPECTED
    findings = advanced_screening_gap(None, fib4, bnp_ordered=False, vcte_ordered=False, patient_id="P1", as_of=AS_OF)
    assert len(findings) == 1
    assert findings[0].domain == ClinicalDomain.LIVER


def test_advanced_screening_fib4_suppressed_when_vcte_already_ordered():
    fib4 = FIB4Calculator().compute(
        FIB4Inputs(patient_id="P1", as_of=AS_OF, age_years=50, ast_u_l=100, alt_u_l=20, platelet_10e9_l=100)
    )
    findings = advanced_screening_gap(None, fib4, bnp_ordered=False, vcte_ordered=True, patient_id="P1", as_of=AS_OF)
    assert findings == []


def test_advanced_screening_fib4_normal_triggers_nothing():
    fib4 = FIB4Calculator().compute(
        FIB4Inputs(patient_id="P1", as_of=AS_OF, age_years=50, ast_u_l=20, alt_u_l=40, platelet_10e9_l=300)
    )
    assert fib4.clinical_status is None
    findings = advanced_screening_gap(None, fib4, bnp_ordered=False, vcte_ordered=False, patient_id="P1", as_of=AS_OF)
    assert findings == []


# ---------------------------------------------------------------------------
# assess_care_gap_agent() 整合
# ---------------------------------------------------------------------------


def test_assess_care_gap_agent_end_to_end():
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, encounters=[])
    profile, care_gap_report, clinical_state = build_reports(state)
    report = assess_care_gap_agent(profile, clinical_state, care_gap_report)
    assert report.patient_id == "P1"
    assert len(report.clinical_clock) == len(CLINICAL_CLOCK_REGISTRY)
    assert len(report.patient_specific_clock) == 3
    assert len(report.p4p_clock) > 0
    # 完全無資料：clinical clock 全數缺紀錄 → 應產生對應 data_gaps
    assert len(report.data_gaps) > 0
    assert any("care_gap_clocks:" in g.source for g in report.data_gaps)
    assert any("非規格書逐字切點" in w for w in report.warnings)


def test_assess_care_gap_agent_detects_bnp_ordered_from_procedures():
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, encounters=[])
    profile, care_gap_report, clinical_state = build_reports(
        state, procedures=(ProcedureRecord(procedure_code=None, procedure_name="NT-proBNP", procedure_date=AS_OF, source="CPOE"),)
    )
    watch_dm = _synthetic_computed_watch_dm(ClinicalStatus.HIGH_RISK)
    report = assess_care_gap_agent(profile, clinical_state, care_gap_report, calculator_results={"WATCH_DM": watch_dm})
    assert report.advanced_screening_gaps == []  # 已開立 NT-proBNP，不再重複建議


def test_procedure_ordered_ignores_future_dated_procedure():
    """回歸測試（Codex 審閱發現的真實 bug）：`_procedure_ordered()` 原本
    未過濾未來日期，會把尚未發生的醫令當成「已開立」，讓
    `advanced_screening_gap()` 誤判已開立而不再提醒（安全方向錯誤：本應
    提醒卻被壓下）。"""
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, encounters=[])
    profile, care_gap_report, clinical_state = build_reports(
        state,
        procedures=(
            ProcedureRecord(
                procedure_code=None, procedure_name="NT-proBNP", procedure_date=AS_OF + timedelta(days=10), source="CPOE"
            ),
        ),
    )
    watch_dm = _synthetic_computed_watch_dm(ClinicalStatus.HIGH_RISK)
    report = assess_care_gap_agent(profile, clinical_state, care_gap_report, calculator_results={"WATCH_DM": watch_dm})
    assert len(report.advanced_screening_gaps) == 1  # 未來日期的醫令不算「已開立」，仍應提醒
