"""
§30/§31 共用型別（`clinical_data_object.py`）測試。

涵蓋情境：
- ClinicalStatus 四態、ClinicalDomain/DOMAIN_DISPLAY_GROUPS 內容
- ModelProvenance 驗證（合法值、非法 taiwan_local_validation_status、空字串拒絕）
- ClinicalFinding 必填欄位（date/generated_at）驗證、frozen 特性
- compose_clinical_data_objects()：無 guideline_report、有命中/未命中規則、
  多規則命中取 priority 最小者、決策合成 action_status/clinician_response
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime

import pytest

from dm_care_pipeline.clinical_data_object import (
    LOCAL_VALIDATION_WARNING,
    ClinicalDomain,
    ClinicalFinding,
    ClinicalStatus,
    DOMAIN_DISPLAY_GROUPS,
    EvidenceItem,
    ModelProvenance,
    SourceSystem,
    compose_clinical_data_objects,
)

AS_OF = date(2024, 6, 1)
NOW = datetime(2024, 6, 1, 12, 0, 0)


def make_finding(**overrides) -> ClinicalFinding:
    defaults = dict(
        finding_id="KIDNEY:CKD:P1:2024-06-01",
        patient_id="P1",
        domain=ClinicalDomain.KIDNEY,
        condition="CKD",
        status=ClinicalStatus.SUSPECTED,
        date=AS_OF,
        generated_at=NOW,
    )
    defaults.update(overrides)
    return ClinicalFinding(**defaults)


# ---------------------------------------------------------------------------
# ClinicalStatus / ClinicalDomain / DOMAIN_DISPLAY_GROUPS
# ---------------------------------------------------------------------------


def test_clinical_status_has_exactly_four_values():
    assert {s.value for s in ClinicalStatus} == {"confirmed", "suspected", "high_risk", "care_gap"}


def test_domain_display_groups_kidney_in_both_microvascular_and_cardiometabolic():
    assert DOMAIN_DISPLAY_GROUPS[ClinicalDomain.KIDNEY] == ("microvascular", "cardiometabolic")
    assert DOMAIN_DISPLAY_GROUPS[ClinicalDomain.GLYCEMIC_CONTROL] == ()


def test_domain_display_groups_covers_every_clinical_domain():
    assert set(DOMAIN_DISPLAY_GROUPS.keys()) == set(ClinicalDomain)


# ---------------------------------------------------------------------------
# ModelProvenance
# ---------------------------------------------------------------------------


def test_model_provenance_accepts_valid_status():
    mp = ModelProvenance(model_name="WATCH-DM", original_population="US T2DM cohort")
    assert mp.taiwan_local_validation_status == "not_locally_validated"
    assert mp.locally_validated is False
    assert mp.warning == LOCAL_VALIDATION_WARNING


def test_model_provenance_locally_validated_property():
    mp = ModelProvenance(
        model_name="WATCH-DM",
        original_population="US T2DM cohort",
        taiwan_local_validation_status="locally_validated",
    )
    assert mp.locally_validated is True


def test_model_provenance_rejects_invalid_status():
    with pytest.raises(ValueError):
        ModelProvenance(model_name="X", original_population="Y", taiwan_local_validation_status="bogus")


def test_model_provenance_rejects_empty_model_name():
    with pytest.raises(ValueError):
        ModelProvenance(model_name="", original_population="Y")


def test_model_provenance_rejects_empty_original_population():
    with pytest.raises(ValueError):
        ModelProvenance(model_name="X", original_population="")


# ---------------------------------------------------------------------------
# ClinicalFinding
# ---------------------------------------------------------------------------


def test_clinical_finding_requires_date():
    with pytest.raises(ValueError):
        ClinicalFinding(
            finding_id="f1",
            patient_id="P1",
            domain=ClinicalDomain.KIDNEY,
            condition="CKD",
            status=ClinicalStatus.SUSPECTED,
            date=None,
            generated_at=NOW,
        )


def test_clinical_finding_requires_generated_at():
    with pytest.raises(ValueError):
        ClinicalFinding(
            finding_id="f1",
            patient_id="P1",
            domain=ClinicalDomain.KIDNEY,
            condition="CKD",
            status=ClinicalStatus.SUSPECTED,
            date=AS_OF,
            generated_at=None,
        )


def test_clinical_finding_is_frozen():
    finding = make_finding()
    with pytest.raises(Exception):
        finding.status = ClinicalStatus.CONFIRMED  # type: ignore[misc]


def test_clinical_finding_defaults():
    finding = make_finding()
    assert finding.action_status == "not_yet_reviewed"
    assert finding.is_placeholder is False
    assert finding.evidence == ()
    assert finding.source == SourceSystem.DERIVED


def test_evidence_item_stringifies_value():
    item = EvidenceItem(label="eGFR", value="52.0", unit="mL/min/1.73m2", source=SourceSystem.LIS)
    assert item.value == "52.0"


# ---------------------------------------------------------------------------
# compose_clinical_data_objects()
# ---------------------------------------------------------------------------


@dataclass
class _FakeState:
    findings: tuple


@dataclass
class _FakeRecommendation:
    related_finding_id: str
    guideline_id: str
    title: str
    priority: object
    recommendation_id: str = ""


@dataclass
class _FakeReport:
    recommendations: tuple


@dataclass
class _FakeDecision:
    recommendation_id: str
    status: object
    free_text_note: str | None = None


@dataclass
class _FakeDecisionRecord:
    decisions: dict  # 真實 PhysicianDecisionRecord.decisions 是 dict[str, PhysicianDecision]


def test_compose_returns_findings_unchanged_when_no_guideline_report():
    state = _FakeState(findings=(make_finding(),))
    composed = compose_clinical_data_objects(state, guideline_report=None)
    assert composed == list(state.findings)


def test_compose_leaves_finding_untouched_when_no_recommendation_hits_it():
    finding = make_finding(finding_id="f1")
    state = _FakeState(findings=(finding,))
    report = _FakeReport(recommendations=())
    composed = compose_clinical_data_objects(state, guideline_report=report)
    assert composed[0].guideline is None
    assert composed[0].recommendation is None
    assert composed[0].action_status == "not_yet_reviewed"


def test_compose_applies_matching_recommendation():
    finding = make_finding(finding_id="f1")
    state = _FakeState(findings=(finding,))
    rec = _FakeRecommendation(
        related_finding_id="f1", guideline_id="KDIGO", title="Start ACEi/ARB", priority=1, recommendation_id="r1"
    )
    report = _FakeReport(recommendations=(rec,))
    composed = compose_clinical_data_objects(state, guideline_report=report)
    assert composed[0].guideline == "KDIGO"
    assert composed[0].recommendation == "Start ACEi/ARB"
    # 原 finding 不被 mutate
    assert finding.guideline is None


def test_compose_picks_lowest_priority_value_when_multiple_recommendations_hit_same_finding():
    finding = make_finding(finding_id="f1")
    state = _FakeState(findings=(finding,))
    rec_low_priority_value = _FakeRecommendation(
        related_finding_id="f1", guideline_id="A", title="urgent", priority=1, recommendation_id="r1"
    )
    rec_high_priority_value = _FakeRecommendation(
        related_finding_id="f1", guideline_id="B", title="routine", priority=5, recommendation_id="r2"
    )
    report = _FakeReport(recommendations=(rec_high_priority_value, rec_low_priority_value))
    composed = compose_clinical_data_objects(state, guideline_report=report)
    assert composed[0].recommendation == "urgent"


def test_compose_picks_urgent_over_routine_with_real_recommendation_priority_enum():
    """回歸測試（Codex 審閱發現的真實 bug）：`RecommendationPriority` 是
    `str, Enum`（值為 "ROUTINE"/"PRIORITY"/"URGENT" 字串，不是數字），原本
    `isinstance(priority_value, (int, float))` 對任何合法優先權皆為 False，
    導致每一筆都落入 999 fallback——排序邏輯形同虛設，實際上永遠是先出現
    者贏。上面 `test_compose_picks_lowest_priority_value_...` 用裸整數
    priority 測試，並未使用真正的 Enum，因此沒有抓到這個 bug；本測試改用
    真正的 `guideline_recommendation.RecommendationPriority`。"""
    from dm_care_pipeline.guideline_recommendation import RecommendationPriority

    finding = make_finding(finding_id="f1")
    state = _FakeState(findings=(finding,))
    rec_routine = _FakeRecommendation(
        related_finding_id="f1", guideline_id="A", title="routine-should-lose", priority=RecommendationPriority.ROUTINE, recommendation_id="r1"
    )
    rec_urgent = _FakeRecommendation(
        related_finding_id="f1", guideline_id="B", title="urgent-should-win", priority=RecommendationPriority.URGENT, recommendation_id="r2"
    )
    # 刻意把 routine 排在 urgent 前面：若排序邏輯壞掉（先出現者贏），
    # 會誤選 routine。
    report = _FakeReport(recommendations=(rec_routine, rec_urgent))
    composed = compose_clinical_data_objects(state, guideline_report=report)
    assert composed[0].recommendation == "urgent-should-win"


def test_compose_applies_decision_action_status_and_note():
    finding = make_finding(finding_id="f1")
    state = _FakeState(findings=(finding,))
    rec = _FakeRecommendation(
        related_finding_id="f1", guideline_id="KDIGO", title="Start ACEi/ARB", priority=1, recommendation_id="r1"
    )
    report = _FakeReport(recommendations=(rec,))
    decision = _FakeDecision(recommendation_id="r1", status="accepted", free_text_note="病人同意")
    decision_record = _FakeDecisionRecord(decisions={"r1": decision})
    composed = compose_clinical_data_objects(state, guideline_report=report, decision_record=decision_record)
    assert composed[0].action_status == "accepted"
    assert composed[0].clinician_response == "病人同意"


def test_compose_works_with_real_physician_decision_record_not_just_fakes():
    """回歸測試（Codex 審閱發現的真實 bug）：`decisions_by_recommendation_id`
    的組裝曾誤把 `decision_record.decisions`（真實型別是
    `dict[str, PhysicianDecision]`）當成可疊代的 decision 序列處理，
    只會走到字典的 key（字串），導致 action_status/clinician_response
    永遠不會被合成。本測試改用真正的
    `physician_decision.PhysicianDecisionRecord`/`present_for_decision()`，
    而非本檔案自訂的 `_FakeDecisionRecord`，確保修正對真實型別成立。"""
    from dm_care_pipeline.guideline_recommendation import (
        EvidenceType,
        GuidelineRecommendation,
        RecommendationEvidence,
        RecommendationPriority,
    )
    from dm_care_pipeline.physician_decision import PhysicianDecision, PhysicianDecisionStatus, present_for_decision

    finding = make_finding(finding_id="f1")
    state = _FakeState(findings=(finding,))
    rec = GuidelineRecommendation(
        recommendation_id="r1",
        rule_id="TEST",
        title="Start ACEi/ARB",
        rationale="test",
        evidence=(RecommendationEvidence(EvidenceType.COMPLICATION, "X", "detail", None),),
        priority=RecommendationPriority.PRIORITY,
        trigger_grounded_in_spec=True,
        action_is_placeholder_content=False,
        related_finding_id="f1",
    )
    report = _FakeReport(recommendations=(rec,))
    decision_record = present_for_decision([rec], patient_id="P1", as_of_date=date(2024, 6, 1))
    decision_record.record_decision(
        PhysicianDecision(
            recommendation_id="r1",
            status=PhysicianDecisionStatus.ACCEPTED,
            free_text_note="病人同意",
            physician_id="DOC1",
        )
    )
    composed = compose_clinical_data_objects(state, guideline_report=report, decision_record=decision_record)
    assert composed[0].action_status == "ACCEPTED"
    assert composed[0].clinician_response == "病人同意"


def test_compose_priority_with_non_numeric_value_treated_as_lowest_priority():
    finding = make_finding(finding_id="f1")
    state = _FakeState(findings=(finding,))
    rec_numeric = _FakeRecommendation(
        related_finding_id="f1", guideline_id="A", title="numeric-wins", priority=2, recommendation_id="r1"
    )
    rec_non_numeric = _FakeRecommendation(
        related_finding_id="f1", guideline_id="B", title="non-numeric-loses", priority=None, recommendation_id="r2"
    )
    report = _FakeReport(recommendations=(rec_non_numeric, rec_numeric))
    composed = compose_clinical_data_objects(state, guideline_report=report)
    assert composed[0].recommendation == "numeric-wins"
