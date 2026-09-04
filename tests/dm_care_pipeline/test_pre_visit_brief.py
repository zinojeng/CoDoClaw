"""
`pre_visit_brief.py`（Pre-Visit Diabetes Brief 組裝層，§21-26）單元測試。
端到端串接（透過 `pipeline.run_stages_1_to_7()`）見
`test_pipeline_integration_v2.py`；此檔案直接建構最小輸入，聚焦
`generate_pre_visit_brief()` 本身的組裝邏輯（鐵律7：不重新計算任何邏輯）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from dm_care_pipeline.calculators.base import CalculatorExecutionStatus, CalculatorResult, CalculatorTier
from dm_care_pipeline.clinical_data_object import ClinicalDomain, ClinicalFinding, ClinicalStatus
from dm_care_pipeline.clinical_state import DomainSummary, PatientClinicalState, TrafficLight
from dm_care_pipeline.guideline_recommendation import (
    GuidelineRecommendation,
    GuidelineRecommendationReport,
    RecommendationPriority,
)
from dm_care_pipeline.medication_intelligence import MedicationRecommendation, MedicationReviewPanel
from dm_care_pipeline.physician_decision import PhysicianDecision, present_for_decision
from dm_care_pipeline.pre_visit_brief import generate_pre_visit_brief
from dm_care_pipeline.trend_analysis import ClinicalTrendReport, MarkerTrend, QualityMetricTier, TrendDirection

AS_OF = date(2024, 6, 1)


@dataclass
class _FakeProfile:
    patient_id: str
    as_of_date: date


def make_finding(finding_id: str, domain: ClinicalDomain, status=ClinicalStatus.CONFIRMED) -> ClinicalFinding:
    return ClinicalFinding(
        finding_id=finding_id,
        patient_id="P1",
        domain=domain,
        condition="test condition",
        status=status,
        date=AS_OF,
        generated_at=datetime.now(),
    )


def make_trend_report() -> ClinicalTrendReport:
    marker = MarkerTrend(
        marker_name="HBA1C",
        item_code_matched="09006C",
        data_points=[(AS_OF, 8.5)],
        direction=TrendDirection.STABLE,
        is_consecutively_worsening=False,
        slope_per_year=None,
        latest_value=8.5,
        latest_result_date=AS_OF,
        control_tier=QualityMetricTier.POOR,
        method_used="single_point",
    )
    return ClinicalTrendReport(patient_id="P1", as_of_date=AS_OF, marker_trends=[marker])


def make_clinical_state(findings: tuple[ClinicalFinding, ...]) -> PatientClinicalState:
    domain_summaries = {
        domain: DomainSummary(domain=domain, traffic_light=TrafficLight.GRAY, headline="no data")
        for domain in ClinicalDomain
    }
    for f in findings:
        domain_summaries[f.domain] = DomainSummary(
            domain=f.domain, traffic_light=TrafficLight.RED, headline=f.condition, finding_ids=(f.finding_id,)
        )
    return PatientClinicalState(
        patient_id="P1", as_of_date=AS_OF, findings=findings, domain_summaries=domain_summaries, data_gaps=[], warnings=[]
    )


def make_guideline_recommendation(rec_id: str) -> GuidelineRecommendation:
    from dm_care_pipeline.guideline_recommendation import EvidenceType, RecommendationEvidence

    return GuidelineRecommendation(
        recommendation_id=rec_id,
        rule_id="TEST_RULE",
        title="test",
        rationale="test",
        evidence=(RecommendationEvidence(EvidenceType.COMPLICATION, "X", "detail", None),),
        priority=RecommendationPriority.PRIORITY,
        trigger_grounded_in_spec=True,
        action_is_placeholder_content=False,
    )


def make_medication_recommendation(rec_id: str) -> MedicationRecommendation:
    return MedicationRecommendation(
        recommendation_id=rec_id,
        rule_id="KIDNEY_PROTECTIVE_THERAPY_GAP",
        title="test med",
        priority=RecommendationPriority.PRIORITY,
        related_finding_id=None,
        review_panel=MedicationReviewPanel(
            indication="test", egfr_value=50.0, egfr_data_gap=False, current_medications=(), contraindications=(),
            guideline_source="ADA_SOC_2026", guideline_section_or_spec_reference="§16",
        ),
        recommended_drug_class="SGLT2_INHIBITOR",
    )


def test_today_widget_keyed_by_marker_name():
    brief = generate_pre_visit_brief(
        _FakeProfile("P1", AS_OF), make_trend_report(), make_clinical_state(()), {},
        GuidelineRecommendationReport(patient_id="P1", as_of_date=AS_OF),
        present_for_decision([], patient_id="P1", as_of_date=AS_OF),
    )
    assert "HBA1C" in brief.today_widget
    assert brief.today_widget["HBA1C"].latest_value == 8.5


def test_trend_widget_directly_references_marker_trends():
    trend_report = make_trend_report()
    brief = generate_pre_visit_brief(
        _FakeProfile("P1", AS_OF), trend_report, make_clinical_state(()), {},
        GuidelineRecommendationReport(patient_id="P1", as_of_date=AS_OF),
        present_for_decision([], patient_id="P1", as_of_date=AS_OF),
    )
    assert brief.trend_widget == tuple(trend_report.marker_trends)


def test_complication_map_built_from_domain_summaries_not_recomputed():
    finding = make_finding("f1", ClinicalDomain.KIDNEY)
    clinical_state = make_clinical_state((finding,))
    brief = generate_pre_visit_brief(
        _FakeProfile("P1", AS_OF), make_trend_report(), clinical_state, {},
        GuidelineRecommendationReport(patient_id="P1", as_of_date=AS_OF),
        present_for_decision([], patient_id="P1", as_of_date=AS_OF),
    )
    assert len(brief.complication_map) == len(clinical_state.domain_summaries)
    kidney_entry = next(e for e in brief.complication_map if e.domain == ClinicalDomain.KIDNEY)
    assert kidney_entry.traffic_light == TrafficLight.RED
    assert kidney_entry.finding_ids == ("f1",)


def test_advanced_risk_widget_passes_calculator_results_as_is():
    result = CalculatorResult(
        calculator_id="KDIGO_GA", calculator_version="v1.0", tier=CalculatorTier.A, patient_id="P1",
        computed_at=AS_OF, execution_status=CalculatorExecutionStatus.COMPUTED, result_values={"g_stage": "G3a"},
    )
    brief = generate_pre_visit_brief(
        _FakeProfile("P1", AS_OF), make_trend_report(), make_clinical_state(()), {"KDIGO_GA": result},
        GuidelineRecommendationReport(patient_id="P1", as_of_date=AS_OF),
        present_for_decision([], patient_id="P1", as_of_date=AS_OF),
    )
    assert brief.advanced_risk_widget == (result,)


def test_evidence_index_keyed_by_finding_id():
    finding = make_finding("f1", ClinicalDomain.EYE)
    clinical_state = make_clinical_state((finding,))
    brief = generate_pre_visit_brief(
        _FakeProfile("P1", AS_OF), make_trend_report(), clinical_state, {},
        GuidelineRecommendationReport(patient_id="P1", as_of_date=AS_OF),
        present_for_decision([], patient_id="P1", as_of_date=AS_OF),
    )
    assert brief.evidence_index == {"f1": finding}


def test_guideline_gap_widget_excludes_medication_recommendations():
    guideline_rec = make_guideline_recommendation("G1")
    medication_rec = make_medication_recommendation("M1")
    decision_record = present_for_decision([guideline_rec, medication_rec], patient_id="P1", as_of_date=AS_OF)
    brief = generate_pre_visit_brief(
        _FakeProfile("P1", AS_OF), make_trend_report(), make_clinical_state(()), {},
        GuidelineRecommendationReport(patient_id="P1", as_of_date=AS_OF, recommendations=[guideline_rec]),
        decision_record,
    )
    assert len(brief.guideline_gap_widget) == 1
    assert brief.guideline_gap_widget[0][0].recommendation_id == "G1"


def test_guideline_gap_widget_pairs_recommendation_with_its_decision():
    guideline_rec = make_guideline_recommendation("G1")
    decision_record = present_for_decision([guideline_rec], patient_id="P1", as_of_date=AS_OF)
    brief = generate_pre_visit_brief(
        _FakeProfile("P1", AS_OF), make_trend_report(), make_clinical_state(()), {},
        GuidelineRecommendationReport(patient_id="P1", as_of_date=AS_OF, recommendations=[guideline_rec]),
        decision_record,
    )
    rec, decision = brief.guideline_gap_widget[0]
    assert decision.recommendation_id == "G1"


def test_alert_report_derived_from_clinical_state_findings():
    finding = make_finding("f1", ClinicalDomain.HEART_FAILURE, status=ClinicalStatus.HIGH_RISK)
    clinical_state = make_clinical_state((finding,))
    brief = generate_pre_visit_brief(
        _FakeProfile("P1", AS_OF), make_trend_report(), clinical_state, {},
        GuidelineRecommendationReport(patient_id="P1", as_of_date=AS_OF),
        present_for_decision([], patient_id="P1", as_of_date=AS_OF),
    )
    assert brief.alert_report.patient_id == "P1"
    from dm_care_pipeline.alert import AlertLevel

    assert finding in brief.alert_report.by_level[AlertLevel.CLINICAL_ATTENTION]


def test_data_gaps_reused_from_clinical_state_not_rebuilt():
    from dm_care_pipeline.pipeline_models import DataGapFlag

    clinical_state = make_clinical_state(())
    clinical_state.data_gaps.append(DataGapFlag(source="test", status="missing", detail="test gap"))
    brief = generate_pre_visit_brief(
        _FakeProfile("P1", AS_OF), make_trend_report(), clinical_state, {},
        GuidelineRecommendationReport(patient_id="P1", as_of_date=AS_OF),
        present_for_decision([], patient_id="P1", as_of_date=AS_OF),
    )
    assert len(brief.data_gaps) == 1
    assert brief.data_gaps[0].source == "test"


def test_care_gap_agent_report_data_gaps_flow_into_brief():
    """回歸測試（Codex #29）：`care_gap_clocks.assess_care_gap_agent()` 的
    `data_gaps` 逐條明確標記 `relevant_downstream_stages=("pre_visit_
    brief",)`，但先前 `generate_pre_visit_brief()` 完全不接受
    `care_gap_agent_report` 參數，這些明確標示要給本站看的缺漏永遠到不了
    這裡。"""
    from dm_care_pipeline.care_gap_clocks import CareGapAgentReport
    from dm_care_pipeline.pipeline_models import DataGapFlag

    clinical_state = make_clinical_state(())
    care_gap_agent_report = CareGapAgentReport(
        patient_id="P1",
        as_of_date=AS_OF,
        clinical_clock=[],
        p4p_clock=[],
        patient_specific_clock=[],
        advanced_screening_gaps=[],
        data_gaps=[
            DataGapFlag(
                source="care_gap_clocks:FOOT_EXAM",
                status="missing",
                detail="查無任何執行紀錄，無法判斷是否逾期",
                relevant_downstream_stages=("pre_visit_brief",),
            ),
            DataGapFlag(
                source="care_gap_clocks:OTHER_STAGE_ONLY",
                status="missing",
                detail="這筆缺漏標記給別的站，不該出現在 pre_visit_brief",
                relevant_downstream_stages=("some_other_stage",),
            ),
        ],
    )
    brief = generate_pre_visit_brief(
        _FakeProfile("P1", AS_OF), make_trend_report(), clinical_state, {},
        GuidelineRecommendationReport(patient_id="P1", as_of_date=AS_OF),
        present_for_decision([], patient_id="P1", as_of_date=AS_OF),
        care_gap_agent_report=care_gap_agent_report,
    )
    gap_sources = {g.source for g in brief.data_gaps}
    assert "care_gap_clocks:FOOT_EXAM" in gap_sources
    assert "care_gap_clocks:OTHER_STAGE_ONLY" not in gap_sources


def test_alert_report_uses_profile_as_of_date_not_finding_evidence_date():
    """回歸測試（Codex #30）：`alert_report.as_of_date` 先前由
    `classify_alert_batch()` 從第一筆 finding 的證據日期（例如檢驗抽血
    日）反推，可能是好幾年前；`generate_pre_visit_brief()` 明明手上有
    `profile.as_of_date`（本次評估的真正日期），應優先採用。"""
    old_evidence_date = date(2019, 1, 1)
    finding = make_finding("f1", ClinicalDomain.KIDNEY)
    finding = ClinicalFinding(
        finding_id=finding.finding_id,
        patient_id=finding.patient_id,
        domain=finding.domain,
        condition=finding.condition,
        status=finding.status,
        date=old_evidence_date,
        generated_at=finding.generated_at,
    )
    clinical_state = make_clinical_state((finding,))
    brief = generate_pre_visit_brief(
        _FakeProfile("P1", AS_OF), make_trend_report(), clinical_state, {},
        GuidelineRecommendationReport(patient_id="P1", as_of_date=AS_OF),
        present_for_decision([], patient_id="P1", as_of_date=AS_OF),
    )
    assert brief.alert_report.as_of_date == AS_OF
    assert brief.alert_report.patient_id == "P1"


def test_alert_report_has_patient_id_and_as_of_date_even_with_no_findings():
    """正向對照：完全無 finding 時，先前 alert_report.patient_id/
    as_of_date 會是 ""/None，即使呼叫端明明知道正確答案。"""
    clinical_state = make_clinical_state(())
    brief = generate_pre_visit_brief(
        _FakeProfile("P1", AS_OF), make_trend_report(), clinical_state, {},
        GuidelineRecommendationReport(patient_id="P1", as_of_date=AS_OF),
        present_for_decision([], patient_id="P1", as_of_date=AS_OF),
    )
    assert brief.alert_report.patient_id == "P1"
    assert brief.alert_report.as_of_date == AS_OF


def test_patient_id_and_as_of_date_come_from_profile():
    brief = generate_pre_visit_brief(
        _FakeProfile("P99", date(2025, 1, 1)), make_trend_report(), make_clinical_state(()), {},
        GuidelineRecommendationReport(patient_id="P99", as_of_date=date(2025, 1, 1)),
        present_for_decision([], patient_id="P99", as_of_date=date(2025, 1, 1)),
    )
    assert brief.patient_id == "P99"
    assert brief.as_of_date == date(2025, 1, 1)
    assert isinstance(brief.generated_at, datetime)
