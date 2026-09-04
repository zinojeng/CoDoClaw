"""
`education.py` v2 擴充測試：規格§27 結構化病人衛教報告
（`EducationSectionCode`/`EducationReportBuilderConfig`/
`generate_patient_education_report()`），以及因 `physician_decision.py`
`Reviewable` 型別放寬而補上的 `select_education_topics()` 防禦性修正。
既有 `select_education_topics()` v1 行為見 `tests/test_care_pipeline.py`，
此檔案不重複覆蓋。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from dm_eligibility.models import PatientEnrollmentState

from dm_care_pipeline.clinical_data_object import ClinicalDomain, ClinicalFinding, ClinicalStatus
from dm_care_pipeline.clinical_state import PatientClinicalState
from dm_care_pipeline.complication_identification import identify_complications
from dm_care_pipeline.data_integration import build_patient_clinical_profile
from dm_care_pipeline.education import (
    EducationReportBuilderConfig,
    EducationSectionCode,
    EducationSectionTemplateRule,
    generate_patient_education_report,
    select_education_topics,
)
from dm_care_pipeline.followup import PendingOrder
from dm_care_pipeline.guideline_recommendation import RecommendationPriority
from dm_care_pipeline.medication_intelligence import MedicationRecommendation, MedicationReviewPanel
from dm_care_pipeline.physician_decision import (
    PhysicianDecision,
    PhysicianDecisionStatus,
    present_for_decision,
)
from dm_care_pipeline.trend_analysis import ClinicalTrendReport, MarkerTrend, QualityMetricTier, TrendDirection

AS_OF = date(2024, 6, 1)


def _profile_and_complication_report():
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, encounters=[])
    profile = build_patient_clinical_profile(state)
    return profile, identify_complications(profile)


def empty_clinical_state(findings=()) -> PatientClinicalState:
    return PatientClinicalState(
        patient_id="P1", as_of_date=AS_OF, findings=tuple(findings), domain_summaries={}, data_gaps=[], warnings=[]
    )


def make_finding(
    status: ClinicalStatus, is_placeholder: bool = False, domain: ClinicalDomain = ClinicalDomain.HEART_FAILURE
) -> ClinicalFinding:
    return ClinicalFinding(
        finding_id="f1",
        patient_id="P1",
        domain=domain,
        condition="test",
        status=status,
        date=AS_OF,
        generated_at=datetime.now(),
        is_placeholder=is_placeholder,
    )


def _med_rec(rec_id: str = "R1") -> MedicationRecommendation:
    return MedicationRecommendation(
        recommendation_id=rec_id,
        rule_id="KIDNEY_PROTECTIVE_THERAPY_GAP",
        title="Kidney-protective therapy gap detected",
        priority=RecommendationPriority.PRIORITY,
        related_finding_id=None,
        review_panel=MedicationReviewPanel(
            indication="test", egfr_value=50.0, egfr_data_gap=False, current_medications=(), contraindications=(),
            guideline_source="ADA_SOC_2026", guideline_section_or_spec_reference="§16",
        ),
        recommended_drug_class="SGLT2_INHIBITOR",
    )


def _hba1c_trend_report(values: list[float]) -> ClinicalTrendReport:
    points = [(AS_OF - timedelta(days=(len(values) - i) * 90), v) for i, v in enumerate(values)]
    marker = MarkerTrend(
        marker_name="HBA1C",
        item_code_matched="09006C",
        data_points=points,
        direction=TrendDirection.RISING,
        is_consecutively_worsening=True,
        slope_per_year=1.0,
        latest_value=values[-1] if values else None,
        latest_result_date=AS_OF,
        control_tier=QualityMetricTier.POOR,
        method_used="linear_regression",
    )
    return ClinicalTrendReport(patient_id="P1", as_of_date=AS_OF, marker_trends=[marker])


def _empty_trend_report() -> ClinicalTrendReport:
    return ClinicalTrendReport(patient_id="P1", as_of_date=AS_OF, marker_trends=[])


# ---------------------------------------------------------------------------
# select_education_topics() 防禦性修正（Reviewable 型別放寬後）
# ---------------------------------------------------------------------------


def test_select_education_topics_skips_medication_recommendation_without_crashing():
    profile, complication_report = _profile_and_complication_report()
    decision_record = present_for_decision([_med_rec()], patient_id="P1", as_of_date=AS_OF)
    decision_record.record_decision(PhysicianDecision(recommendation_id="R1", status=PhysicianDecisionStatus.ACCEPTED))
    plan = select_education_topics(decision_record, complication_report)
    # MedicationRecommendation 沒有 education_topic_code，應被安全略過，不 crash
    assert plan.topics == []


# ---------------------------------------------------------------------------
# generate_patient_education_report()
# ---------------------------------------------------------------------------


def test_cardiac_section_triggers_on_high_risk_without_numeric_disclosure():
    finding = make_finding(ClinicalStatus.HIGH_RISK)
    clinical_state = empty_clinical_state([finding])
    profile, complication_report = _profile_and_complication_report()
    decision_record = present_for_decision([], patient_id="P1", as_of_date=AS_OF)
    report = generate_patient_education_report(clinical_state, _empty_trend_report(), complication_report, decision_record)
    cardiac_sections = [s for s in report.sections if s.section_code == EducationSectionCode.CARDIAC]
    assert len(cardiac_sections) == 1
    assert "心臟" in cardiac_sections[0].body_text
    assert report.needs_manual_review is True  # is_placeholder=True 範例模板


def test_glycemic_section_skipped_when_fewer_than_three_hba1c_points():
    finding = make_finding(ClinicalStatus.HIGH_RISK, domain=ClinicalDomain.GLYCEMIC_CONTROL)
    clinical_state = empty_clinical_state([finding])
    profile, complication_report = _profile_and_complication_report()
    decision_record = present_for_decision([], patient_id="P1", as_of_date=AS_OF)
    trend_report = _hba1c_trend_report([8.0, 8.5])  # 只有2筆
    report = generate_patient_education_report(clinical_state, trend_report, complication_report, decision_record)
    glycemic_sections = [s for s in report.sections if s.section_code == EducationSectionCode.GLYCEMIC]
    assert glycemic_sections == []
    assert any("不足3筆" in w for w in report.warnings)


def test_glycemic_section_renders_real_values_with_three_hba1c_points():
    finding = make_finding(ClinicalStatus.HIGH_RISK, domain=ClinicalDomain.GLYCEMIC_CONTROL)
    clinical_state = empty_clinical_state([finding])
    profile, complication_report = _profile_and_complication_report()
    decision_record = present_for_decision([], patient_id="P1", as_of_date=AS_OF)
    trend_report = _hba1c_trend_report([7.5, 8.0, 8.5])
    report = generate_patient_education_report(clinical_state, trend_report, complication_report, decision_record)
    glycemic_sections = [s for s in report.sections if s.section_code == EducationSectionCode.GLYCEMIC]
    assert len(glycemic_sections) == 1
    assert "7.5" in glycemic_sections[0].body_text
    assert "8.0" in glycemic_sections[0].body_text
    assert "8.5" in glycemic_sections[0].body_text


def test_placeholder_finding_never_gets_numeric_disclosure_template():
    finding = make_finding(ClinicalStatus.CARE_GAP, is_placeholder=True, domain=ClinicalDomain.GLYCEMIC_CONTROL)
    clinical_state = empty_clinical_state([finding])
    profile, complication_report = _profile_and_complication_report()
    decision_record = present_for_decision([], patient_id="P1", as_of_date=AS_OF)
    trend_report = _hba1c_trend_report([7.5, 8.0, 8.5])
    report = generate_patient_education_report(clinical_state, trend_report, complication_report, decision_record)
    # CARE_GAP 命中 GLYCEMIC 規則（requires_numeric_disclosure=True），但
    # finding.is_placeholder=True，鐵律4強制不套用該模板
    glycemic_sections = [s for s in report.sections if s.section_code == EducationSectionCode.GLYCEMIC]
    assert glycemic_sections == []


def test_no_matching_status_produces_no_sections():
    finding = make_finding(ClinicalStatus.CONFIRMED)
    clinical_state = empty_clinical_state([finding])
    profile, complication_report = _profile_and_complication_report()
    decision_record = present_for_decision([], patient_id="P1", as_of_date=AS_OF)
    report = generate_patient_education_report(clinical_state, _empty_trend_report(), complication_report, decision_record)
    assert report.sections == []


def test_today_actions_includes_accepted_recommendations_and_pending_orders():
    clinical_state = empty_clinical_state()
    profile, complication_report = _profile_and_complication_report()
    decision_record = present_for_decision([_med_rec()], patient_id="P1", as_of_date=AS_OF)
    decision_record.record_decision(PhysicianDecision(recommendation_id="R1", status=PhysicianDecisionStatus.ACCEPTED))
    pending_order = PendingOrder(order_id="O1", order_type="FIBROSCAN", ordered_date=AS_OF, status="ORDERED")
    report = generate_patient_education_report(
        clinical_state, _empty_trend_report(), complication_report, decision_record, pending_orders=(pending_order,)
    )
    assert any("Kidney-protective" in a for a in report.today_actions)
    assert any("FIBROSCAN" in a for a in report.today_actions)


def test_today_actions_uses_modified_action_text_not_original_title():
    """回歸測試（Codex #27）：status==MODIFIED 時，先前 today_actions 一律
    顯示原始建議的 rec.title，即使醫師已經修改成別的內容——病人衛教內容
    會顯示醫師「已修改但從未真正採用」的原始建議，而非醫師實際核可的
    modified_action_text。"""
    clinical_state = empty_clinical_state()
    profile, complication_report = _profile_and_complication_report()
    decision_record = present_for_decision([_med_rec()], patient_id="P1", as_of_date=AS_OF)
    decision_record.record_decision(
        PhysicianDecision(
            recommendation_id="R1",
            status=PhysicianDecisionStatus.MODIFIED,
            modified_action_text="改開 GLP-1 RA 而非 SGLT2i",
        )
    )
    report = generate_patient_education_report(clinical_state, _empty_trend_report(), complication_report, decision_record)
    assert any("改開 GLP-1 RA 而非 SGLT2i" in a for a in report.today_actions)
    assert not any("Kidney-protective" in a for a in report.today_actions)


def test_completed_pending_order_not_listed_as_today_action():
    clinical_state = empty_clinical_state()
    profile, complication_report = _profile_and_complication_report()
    decision_record = present_for_decision([], patient_id="P1", as_of_date=AS_OF)
    completed_order = PendingOrder(order_id="O1", order_type="ECHO", ordered_date=AS_OF, status="COMPLETED")
    report = generate_patient_education_report(
        clinical_state, _empty_trend_report(), complication_report, decision_record, pending_orders=(completed_order,)
    )
    assert not any("ECHO" in a for a in report.today_actions)


def test_cancelled_pending_order_not_listed_as_today_action():
    """回歸測試（Codex 審閱發現的真實 bug）：原本「非COMPLETED即待完成」
    會把 CANCELLED 也顯示成「待完成醫令」，誤導病人。"""
    clinical_state = empty_clinical_state()
    profile, complication_report = _profile_and_complication_report()
    decision_record = present_for_decision([], patient_id="P1", as_of_date=AS_OF)
    cancelled_order = PendingOrder(order_id="O1", order_type="FIBROSCAN", ordered_date=AS_OF, status="CANCELLED")
    report = generate_patient_education_report(
        clinical_state, _empty_trend_report(), complication_report, decision_record, pending_orders=(cancelled_order,)
    )
    assert not any("FIBROSCAN" in a for a in report.today_actions)


def test_resource_topics_reuse_select_education_topics_output():
    clinical_state = empty_clinical_state()
    profile, complication_report = _profile_and_complication_report()
    decision_record = present_for_decision([], patient_id="P1", as_of_date=AS_OF)
    report = generate_patient_education_report(clinical_state, _empty_trend_report(), complication_report, decision_record)
    resource_plan = select_education_topics(decision_record, complication_report)
    assert [t.topic_code for t in report.resource_topics] == [t.topic_code for t in resource_plan.topics]


def test_custom_config_can_disable_all_sections():
    finding = make_finding(ClinicalStatus.HIGH_RISK)
    clinical_state = empty_clinical_state([finding])
    profile, complication_report = _profile_and_complication_report()
    decision_record = present_for_decision([], patient_id="P1", as_of_date=AS_OF)
    cfg = EducationReportBuilderConfig(section_rules=())
    report = generate_patient_education_report(
        clinical_state, _empty_trend_report(), complication_report, decision_record, config=cfg
    )
    assert report.sections == []


def test_cross_domain_finding_never_triggers_unrelated_section_template():
    """回歸測試（Codex 審閱發現的真實 bug）：原本只比對 status，未檢查
    finding.domain 是否對應該 section——FOOT 領域的 HIGH_RISK finding
    會被誤套用 CARDIAC 心衰竭衛教文案，對病人產生無關的說明。"""
    foot_finding = make_finding(ClinicalStatus.HIGH_RISK, domain=ClinicalDomain.FOOT)
    clinical_state = empty_clinical_state([foot_finding])
    profile, complication_report = _profile_and_complication_report()
    decision_record = present_for_decision([], patient_id="P1", as_of_date=AS_OF)
    report = generate_patient_education_report(clinical_state, _empty_trend_report(), complication_report, decision_record)
    # 沒有任何 FOOT 對應的 section_rule（預設只有 GLYCEMIC/CARDIAC），
    # 故不應誤套用 CARDIAC 心衰竭文案。
    assert not any(s.section_code == EducationSectionCode.CARDIAC for s in report.sections)
