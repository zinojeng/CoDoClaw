"""
`physician_decision.py` v2 擴充測試：`Reviewable` Protocol、
`PhysicianDecision.decline_category`、`present_for_decision()` 簽名放寬。
既有 v1 行為（`record_decision()` 驗證/`accepted_or_modified()`/
`to_audit_trail()`）見 `tests/test_care_pipeline.py`，此檔案不重複覆蓋。
"""

from __future__ import annotations

from datetime import date

import pytest

from dm_care_pipeline.guideline_recommendation import (
    GuidelineRecommendationReport,
    RecommendationPriority,
)
from dm_care_pipeline.medication_intelligence import (
    MedicationIntelligenceReport,
    MedicationRecommendation,
    MedicationReviewPanel,
)
from dm_care_pipeline.physician_decision import (
    DecisionValidationError,
    PhysicianDecision,
    PhysicianDecisionStatus,
    Reviewable,
    present_for_decision,
)

AS_OF = date(2024, 6, 1)


def _med_rec(rec_id: str) -> MedicationRecommendation:
    return MedicationRecommendation(
        recommendation_id=rec_id,
        rule_id="KIDNEY_PROTECTIVE_THERAPY_GAP",
        title="Kidney-protective therapy gap detected",
        priority=RecommendationPriority.PRIORITY,
        related_finding_id=None,
        review_panel=MedicationReviewPanel(
            indication="test",
            egfr_value=50.0,
            egfr_data_gap=False,
            current_medications=(),
            contraindications=(),
            guideline_source="ADA_SOC_2026",
            guideline_section_or_spec_reference="§16",
        ),
        recommended_drug_class="SGLT2_INHIBITOR",
    )


# ---------------------------------------------------------------------------
# Reviewable Protocol
# ---------------------------------------------------------------------------


def test_medication_recommendation_satisfies_reviewable_protocol():
    rec = _med_rec("R1")
    assert isinstance(rec, Reviewable)


# ---------------------------------------------------------------------------
# PhysicianDecision.decline_category
# ---------------------------------------------------------------------------


def test_decline_category_defaults_to_none():
    decision = PhysicianDecision(recommendation_id="R1")
    assert decision.decline_category is None


def test_decline_category_can_be_set_for_declined_status():
    decision = PhysicianDecision(
        recommendation_id="R1",
        status=PhysicianDecisionStatus.DECLINED,
        decline_reason="not indicated",
        decline_category="not_applicable",
    )
    assert decision.decline_category == "not_applicable"


# ---------------------------------------------------------------------------
# present_for_decision() 型別放寬
# ---------------------------------------------------------------------------


def test_present_for_decision_accepts_medication_intelligence_report():
    report = MedicationIntelligenceReport(patient_id="P1", as_of_date=AS_OF, recommendations=[_med_rec("R1")])
    record = present_for_decision(report)
    assert record.patient_id == "P1"
    assert record.as_of_date == AS_OF
    assert record.pending_count() == 1


def test_present_for_decision_accepts_bare_sequence_with_explicit_patient_and_date():
    recommendations = [_med_rec("R1"), _med_rec("R2")]
    record = present_for_decision(recommendations, patient_id="P1", as_of_date=AS_OF)
    assert record.patient_id == "P1"
    assert record.pending_count() == 2


def test_present_for_decision_bare_sequence_without_patient_id_raises():
    recommendations = [_med_rec("R1")]
    with pytest.raises(DecisionValidationError):
        present_for_decision(recommendations, as_of_date=AS_OF)


def test_present_for_decision_bare_sequence_without_as_of_date_raises():
    recommendations = [_med_rec("R1")]
    with pytest.raises(DecisionValidationError):
        present_for_decision(recommendations, patient_id="P1")


def test_present_for_decision_merges_guideline_and_medication_recommendations():
    guideline_report = GuidelineRecommendationReport(patient_id="P1", as_of_date=AS_OF, recommendations=[])
    medication_report = MedicationIntelligenceReport(
        patient_id="P1", as_of_date=AS_OF, recommendations=[_med_rec("R1"), _med_rec("R2")]
    )
    merged = [*guideline_report.recommendations, *medication_report.recommendations]
    record = present_for_decision(merged, patient_id="P1", as_of_date=AS_OF)
    assert record.pending_count() == 2
    assert {r.recommendation_id for r, _ in record.accepted_or_modified()} == set()  # 全 PENDING，尚未有 accepted


def test_present_for_decision_still_works_with_guideline_report_positionally():
    report = GuidelineRecommendationReport(patient_id="P1", as_of_date=AS_OF, recommendations=[])
    record = present_for_decision(report)
    assert record.patient_id == "P1"
    assert record.presented_recommendations == ()


def test_present_for_decision_still_works_with_guideline_report_keyword():
    """回歸測試（Codex 審閱發現的真實 bug）：v2 一度把第一參數改名為
    `source`，導致既有的 `present_for_decision(report=...)` 關鍵字呼叫
    TypeError；已修正回保留原參數名 `report`。"""
    report = GuidelineRecommendationReport(patient_id="P1", as_of_date=AS_OF, recommendations=[])
    record = present_for_decision(report=report)
    assert record.patient_id == "P1"


def test_duplicate_recommendation_id_across_sources_raises_instead_of_silently_merging():
    """回歸測試（Codex #24）：guideline_recommendation.py 與
    medication_intelligence.py 是兩份各自獨立的 rule_id 命名空間，先前組
    recommendation_id 的公式完全相同（rule_id::patient::date），若自訂
    規則的 rule_id 剛好撞名，兩筆本應獨立的建議會在 decisions dict（以
    recommendation_id 為 key）被靜默合併成一筆，醫師對其中一筆的決策會
    被誤讀成對另一筆的決策。建構 PhysicianDecisionRecord 時應直接擋下。"""
    colliding_id = "SAME_RULE_ID::P1::2024-06-01"
    rec_a = _med_rec(colliding_id)
    rec_b = MedicationRecommendation(
        recommendation_id=colliding_id,  # 故意撞名，模擬另一個來源的建議
        rule_id="A_DIFFERENT_RULE",
        title="An entirely independent recommendation",
        priority=RecommendationPriority.ROUTINE,
        related_finding_id=None,
        review_panel=rec_a.review_panel,
    )
    with pytest.raises(DecisionValidationError, match="重複出現"):
        present_for_decision([rec_a, rec_b], patient_id="P1", as_of_date=AS_OF)


def test_record_decision_and_accepted_or_modified_works_across_mixed_types():
    medication_report = MedicationIntelligenceReport(
        patient_id="P1", as_of_date=AS_OF, recommendations=[_med_rec("R1")]
    )
    record = present_for_decision(medication_report)
    record.record_decision(PhysicianDecision(recommendation_id="R1", status=PhysicianDecisionStatus.ACCEPTED))
    accepted = record.accepted_or_modified()
    assert len(accepted) == 1
    assert accepted[0][0].recommendation_id == "R1"
