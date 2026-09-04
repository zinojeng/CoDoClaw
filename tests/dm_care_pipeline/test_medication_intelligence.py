"""
`medication_intelligence.py`（Medication Intelligence Agent，§16-17）測試。
"""

from __future__ import annotations

from datetime import date, datetime

from dm_eligibility.models import Encounter, MedicationOrder, PatientEnrollmentState

from dm_care_pipeline.calculators.base import CalculatorExecutionStatus, CalculatorResult, CalculatorTier
from dm_care_pipeline.calculators.ckd_ga import CKDGACalculator, CKDGAInputs
from dm_care_pipeline.clinical_data_object import ClinicalDomain, ClinicalFinding, ClinicalStatus
from dm_care_pipeline.clinical_state import PatientClinicalState
from dm_care_pipeline.data_integration import build_patient_clinical_profile
from dm_care_pipeline.guideline_recommendation import RecommendationPriority
from dm_care_pipeline.medication_intelligence import (
    MEDICATION_ATC_CLASS_MAP,
    ContraindicationFlag,
    MedicationCheckInput,
    MedicationRecommendation,
    MedicationReviewPanel,
    NullContraindicationChecker,
    assess_ada_level1_hypoglycemia_risk,
    build_medication_check_input,
    build_medication_intelligence_report,
    build_medication_order_draft,
    default_medication_indication_rules,
)
from dm_care_pipeline.physician_decision import PhysicianDecision, PhysicianDecisionStatus

AS_OF = date(2024, 6, 1)


def empty_state(active_drug_classes=frozenset()) -> PatientClinicalState:
    return PatientClinicalState(
        patient_id="P1", as_of_date=AS_OF, findings=(), domain_summaries={}, data_gaps=[], warnings=[]
    )


def state_with_findings(*findings: ClinicalFinding) -> PatientClinicalState:
    return PatientClinicalState(
        patient_id="P1", as_of_date=AS_OF, findings=findings, domain_summaries={}, data_gaps=[], warnings=[]
    )


def make_finding(domain: ClinicalDomain, condition: str, status=ClinicalStatus.CONFIRMED) -> ClinicalFinding:
    return ClinicalFinding(
        finding_id=f"{domain.value}:{condition}:P1:{AS_OF.isoformat()}",
        patient_id="P1",
        domain=domain,
        condition=condition,
        status=status,
        date=AS_OF,
        generated_at=datetime.now(),
    )


def make_check_input(**overrides) -> MedicationCheckInput:
    defaults = dict(
        patient_id="P1",
        as_of_date=AS_OF,
        active_drug_classes=frozenset(),
        clinical_state=empty_state(),
        kdigo_g_stage=None,
        kdigo_a_stage=None,
        age_years=60,
        hypoglycemia_level1_result=None,
        data_gaps=[],
    )
    defaults.update(overrides)
    return MedicationCheckInput(**defaults)


def make_dm_encounter(atc_codes: tuple[str, ...]) -> Encounter:
    return Encounter(
        encounter_id="E1",
        visit_date=AS_OF,
        physician_id="DOC1",
        medication_orders=tuple(MedicationOrder(code) for code in atc_codes),
    )


# ---------------------------------------------------------------------------
# MEDICATION_ATC_CLASS_MAP / build_medication_check_input()
# ---------------------------------------------------------------------------


def test_medication_atc_class_map_has_eight_classes():
    assert set(MEDICATION_ATC_CLASS_MAP.keys()) == {
        "SGLT2_INHIBITOR",
        "GLP1_RA",
        "METFORMIN",
        "SULFONYLUREA",
        "MEGLITINIDE",
        "DPP4_INHIBITOR",
        "TZD",
        "INSULIN",
    }


def test_build_medication_check_input_maps_atc_codes_to_drug_classes():
    state = PatientEnrollmentState(
        patient_id="P1", as_of_date=AS_OF, encounters=[make_dm_encounter(("A10BK01", "A10BA02"))]
    )
    profile = build_patient_clinical_profile(state)
    clinical_state = empty_state()
    inp = build_medication_check_input(profile, clinical_state)
    assert inp.active_drug_classes == {"SGLT2_INHIBITOR", "METFORMIN"}


def test_meglitinide_mapping_excludes_non_meglitinide_a10bx_drugs():
    """回歸測試（Codex #16）：A10BX 是 WHO ATC「其他降血糖藥」子類，不是
    meglitinide 專屬前綴——guar gum（例如 A10BX01）等非 meglitinide 藥物
    先前也會被整段 A10BX 前綴誤標成 meglitinide。"""
    state = PatientEnrollmentState(
        patient_id="P1", as_of_date=AS_OF, encounters=[make_dm_encounter(("A10BX01",))]
    )
    profile = build_patient_clinical_profile(state)
    inp = build_medication_check_input(profile, empty_state())
    assert "MEGLITINIDE" not in inp.active_drug_classes


def test_meglitinide_mapping_still_includes_known_meglitinide_codes():
    """正向對照：repaglinide（A10BX02）/nateglinide（A10BX03）仍應被正確
    分類為 meglitinide。"""
    state = PatientEnrollmentState(
        patient_id="P1", as_of_date=AS_OF, encounters=[make_dm_encounter(("A10BX02",))]
    )
    profile = build_patient_clinical_profile(state)
    inp = build_medication_check_input(profile, empty_state())
    assert "MEGLITINIDE" in inp.active_drug_classes


def test_combination_product_flagged_as_data_gap_not_silently_dropped():
    """回歸測試（Codex #16）：A10BD 複方降血糖製劑（如
    metformin/SGLT2i 複方）不會匹配任何單一藥物類別前綴，先前完全靜默
    消失——病人明明有用藥，卻跟「查過確認沒用藥」看起來一樣。現在應在
    data_gaps 明確標記，且流入 MedicationIntelligenceReport.warnings。"""
    state = PatientEnrollmentState(
        patient_id="P1", as_of_date=AS_OF, encounters=[make_dm_encounter(("A10BD25",))]
    )
    profile = build_patient_clinical_profile(state)
    inp = build_medication_check_input(profile, empty_state())
    assert inp.active_drug_classes == frozenset()  # 複方成分本身仍未拆解（誠實回報，非本檔案片面決定）
    gap_sources = {g.source for g in inp.data_gaps}
    assert "active_medication_atc_codes" in gap_sources

    report = build_medication_intelligence_report(inp)
    assert any("A10BD" in w for w in report.warnings)


def test_build_medication_check_input_extracts_kdigo_stage_and_egfr_from_calculator_result():
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, encounters=[])
    profile = build_patient_clinical_profile(state)
    ckd_result = CKDGACalculator().compute(CKDGAInputs(patient_id="P1", as_of=AS_OF, egfr=50.0, uacr=100.0))
    inp = build_medication_check_input(profile, empty_state(), calculator_results={"KDIGO_GA": ckd_result})
    assert inp.kdigo_g_stage == "G3a"
    assert inp.kdigo_a_stage == "A2"
    assert inp.egfr_value == 50.0


def test_build_medication_check_input_falls_back_to_thin_wrapper_when_ada_hypo_not_provided():
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, encounters=[])
    profile = build_patient_clinical_profile(state)
    inp = build_medication_check_input(profile, empty_state())
    assert inp.hypoglycemia_level1_result is not None
    # 未提供 major/minor factors 評估結果，忠實回報 INSUFFICIENT_DATA（不臆測）
    assert inp.hypoglycemia_level1_result.execution_status in (
        CalculatorExecutionStatus.INSUFFICIENT_DATA,
        CalculatorExecutionStatus.COMPUTED,
    )


def test_build_medication_check_input_reuses_provided_ada_hypo_result():
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, encounters=[])
    profile = build_patient_clinical_profile(state)
    fake_result = CalculatorResult(
        calculator_id="ADA_HYPO_L1",
        calculator_version="v1.0",
        tier=CalculatorTier.A,
        patient_id="P1",
        computed_at=AS_OF,
        execution_status=CalculatorExecutionStatus.COMPUTED,
        result_values={"risk_level": "HIGH"},
        clinical_status=ClinicalStatus.HIGH_RISK,
    )
    inp = build_medication_check_input(profile, empty_state(), calculator_results={"ADA_HYPO_L1": fake_result})
    assert inp.hypoglycemia_level1_result is fake_result


def test_assess_ada_level1_hypoglycemia_risk_thin_wrapper():
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, encounters=[])
    profile = build_patient_clinical_profile(state)
    result = assess_ada_level1_hypoglycemia_risk(
        profile,
        frozenset({"INSULIN"}),
        major_factors=frozenset({"kidney_failure"}),
        risk_factors_assessed=True,
        has_medication_data=True,
    )
    assert result.execution_status == CalculatorExecutionStatus.COMPUTED
    assert result.clinical_status == ClinicalStatus.HIGH_RISK


def test_assess_ada_level1_hypoglycemia_risk_no_medication_data_is_insufficient_data():
    """回歸測試（Codex #14）：has_medication_data 預設 False 時，即使
    active_drug_classes 恰好是空集合，也不可默視為『確認未使用』。"""
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, encounters=[])
    profile = build_patient_clinical_profile(state)
    result = assess_ada_level1_hypoglycemia_risk(profile, frozenset())
    assert result.execution_status == CalculatorExecutionStatus.INSUFFICIENT_DATA


# ---------------------------------------------------------------------------
# 三條內建規則
# ---------------------------------------------------------------------------


def test_kidney_protective_therapy_gap_triggers_when_ckd_and_no_protective_drug():
    inp = make_check_input(kdigo_g_stage="G3a", kdigo_a_stage="A3", active_drug_classes=frozenset({"METFORMIN"}))
    report = build_medication_intelligence_report(inp)
    hit = next(r for r in report.recommendations if r.rule_id == "KIDNEY_PROTECTIVE_THERAPY_GAP")
    assert hit.recommended_drug_class == "SGLT2_INHIBITOR"
    assert "Kidney-protective therapy gap detected" in hit.review_panel.indication


def test_recommendation_id_has_medication_namespace_prefix():
    """回歸測試（Codex #24），medication_intelligence.py 對照版本。"""
    inp = make_check_input(kdigo_g_stage="G3a", kdigo_a_stage="A3", active_drug_classes=frozenset({"METFORMIN"}))
    report = build_medication_intelligence_report(inp)
    hit = next(r for r in report.recommendations if r.rule_id == "KIDNEY_PROTECTIVE_THERAPY_GAP")
    assert hit.recommendation_id.startswith("medication:KIDNEY_PROTECTIVE_THERAPY_GAP::")


def test_kidney_protective_therapy_gap_silent_when_sglt2i_present():
    inp = make_check_input(kdigo_g_stage="G3a", kdigo_a_stage="A3", active_drug_classes=frozenset({"SGLT2_INHIBITOR"}))
    report = build_medication_intelligence_report(inp)
    assert not any(r.rule_id == "KIDNEY_PROTECTIVE_THERAPY_GAP" for r in report.recommendations)


def test_kidney_protective_therapy_gap_silent_when_glp1ra_present():
    inp = make_check_input(kdigo_g_stage="G3a", kdigo_a_stage="A3", active_drug_classes=frozenset({"GLP1_RA"}))
    report = build_medication_intelligence_report(inp)
    assert not any(r.rule_id == "KIDNEY_PROTECTIVE_THERAPY_GAP" for r in report.recommendations)


def test_kidney_protective_therapy_gap_silent_without_ckd():
    inp = make_check_input(kdigo_g_stage="G1", kdigo_a_stage="A1", active_drug_classes=frozenset())
    report = build_medication_intelligence_report(inp)
    assert not any(r.rule_id == "KIDNEY_PROTECTIVE_THERAPY_GAP" for r in report.recommendations)


def test_kidney_protective_therapy_gap_silent_for_g2a1_not_ckd():
    """回歸測試（Codex #13）：_has_ckd() 先前把「非 G1」都當 CKD，G2A1
    （eGFR 60-89、UACR 正常，不符 KDIGO CKD 定義）會被誤判有 CKD，進而
    在未使用 SGLT2i/GLP-1RA 時觸發不必要的腎臟保護治療缺口建議。"""
    inp = make_check_input(kdigo_g_stage="G2", kdigo_a_stage="A1", active_drug_classes=frozenset())
    report = build_medication_intelligence_report(inp)
    assert not any(r.rule_id == "KIDNEY_PROTECTIVE_THERAPY_GAP" for r in report.recommendations)


def test_secondary_ascvd_prevention_gap_triggers_on_confirmed_finding():
    finding = make_finding(ClinicalDomain.CEREBROVASCULAR, "腦血管疾病")
    inp = make_check_input(clinical_state=state_with_findings(finding))
    report = build_medication_intelligence_report(inp)
    hit = next(r for r in report.recommendations if r.rule_id == "SECONDARY_ASCVD_PREVENTION_GAP")
    assert hit.related_finding_id == finding.finding_id
    assert hit.recommended_drug_class is None  # 範例未點名具體藥物，不臆造


def test_secondary_ascvd_prevention_gap_silent_without_ascvd_finding():
    inp = make_check_input(clinical_state=empty_state())
    report = build_medication_intelligence_report(inp)
    assert not any(r.rule_id == "SECONDARY_ASCVD_PREVENTION_GAP" for r in report.recommendations)


def test_high_hypoglycemia_risk_deintensification_triggers():
    hypo_result = CalculatorResult(
        calculator_id="ADA_HYPO_L1",
        calculator_version="v1.0",
        tier=CalculatorTier.A,
        patient_id="P1",
        computed_at=AS_OF,
        execution_status=CalculatorExecutionStatus.COMPUTED,
        result_values={"risk_level": "HIGH"},
        result_summary="Hypoglycemia risk: HIGH",
        clinical_status=ClinicalStatus.HIGH_RISK,
    )
    inp = make_check_input(
        active_drug_classes=frozenset({"SULFONYLUREA", "INSULIN"}), hypoglycemia_level1_result=hypo_result
    )
    report = build_medication_intelligence_report(inp)
    hit = next(r for r in report.recommendations if r.rule_id == "HIGH_HYPOGLYCEMIA_RISK_DEINTENSIFICATION")
    assert hit.priority == RecommendationPriority.URGENT
    assert hit.recommended_drug_class is None
    assert "deintensification" in hit.review_panel.indication


def test_high_hypoglycemia_risk_silent_when_not_on_risky_meds():
    hypo_result = CalculatorResult(
        calculator_id="ADA_HYPO_L1",
        calculator_version="v1.0",
        tier=CalculatorTier.A,
        patient_id="P1",
        computed_at=AS_OF,
        execution_status=CalculatorExecutionStatus.COMPUTED,
        result_values={"risk_level": "HIGH"},
        clinical_status=ClinicalStatus.HIGH_RISK,
    )
    inp = make_check_input(active_drug_classes=frozenset({"METFORMIN"}), hypoglycemia_level1_result=hypo_result)
    report = build_medication_intelligence_report(inp)
    assert not any(r.rule_id == "HIGH_HYPOGLYCEMIA_RISK_DEINTENSIFICATION" for r in report.recommendations)


# ---------------------------------------------------------------------------
# NullContraindicationChecker / review_panel
# ---------------------------------------------------------------------------


def test_null_contraindication_checker_always_not_evaluated():
    inp = make_check_input(kdigo_g_stage="G3a", kdigo_a_stage="A3")
    flags = NullContraindicationChecker().check(inp, "SGLT2_INHIBITOR")
    assert len(flags) == 1
    assert flags[0].status == "not_evaluated"


def test_report_review_panel_includes_egfr_and_contraindications():
    inp = make_check_input(kdigo_g_stage="G3a", kdigo_a_stage="A3", egfr_value=50.0)
    report = build_medication_intelligence_report(inp)
    hit = next(r for r in report.recommendations if r.rule_id == "KIDNEY_PROTECTIVE_THERAPY_GAP")
    assert hit.review_panel.egfr_value == 50.0
    assert hit.review_panel.egfr_data_gap is False
    assert len(hit.review_panel.contraindications) == 2  # SGLT2_INHIBITOR + GLP1_RA candidates
    assert all(c.status == "not_evaluated" for c in hit.review_panel.contraindications)


def test_report_egfr_data_gap_true_when_egfr_missing():
    inp = make_check_input(kdigo_g_stage="G3a", kdigo_a_stage="A3", egfr_value=None)
    report = build_medication_intelligence_report(inp)
    hit = next(r for r in report.recommendations if r.rule_id == "KIDNEY_PROTECTIVE_THERAPY_GAP")
    assert hit.review_panel.egfr_data_gap is True


def test_matcher_exception_recorded_as_warning_not_crash():
    def broken_matcher(inp):
        raise RuntimeError("boom")

    from dm_care_pipeline.medication_intelligence import MedicationIndicationRule

    rule = MedicationIndicationRule(
        rule_id="BROKEN",
        guideline_id="ADA_SOC_2026",
        title_template="broken",
        matcher=broken_matcher,
        priority=RecommendationPriority.ROUTINE,
        trigger_grounded_in_spec=True,
        action_is_placeholder_content=False,
        spec_reference="",
    )
    report = build_medication_intelligence_report(make_check_input(), rules=[rule])
    assert report.recommendations == []
    assert any("BROKEN" in w for w in report.warnings)


# ---------------------------------------------------------------------------
# build_medication_order_draft()（鐵律4）
# ---------------------------------------------------------------------------


def _sample_recommendation(recommended_drug_class="SGLT2_INHIBITOR") -> MedicationRecommendation:
    return MedicationRecommendation(
        recommendation_id="R1",
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
        recommended_drug_class=recommended_drug_class,
    )


def test_order_draft_none_when_pending():
    decision = PhysicianDecision(recommendation_id="R1", status=PhysicianDecisionStatus.PENDING)
    draft = build_medication_order_draft(_sample_recommendation(), decision, _sample_recommendation().review_panel)
    assert draft is None


def test_order_draft_none_when_declined():
    decision = PhysicianDecision(recommendation_id="R1", status=PhysicianDecisionStatus.DECLINED, decline_reason="not indicated")
    draft = build_medication_order_draft(_sample_recommendation(), decision, _sample_recommendation().review_panel)
    assert draft is None


def test_order_draft_none_when_recommended_drug_class_is_none():
    decision = PhysicianDecision(recommendation_id="R1", status=PhysicianDecisionStatus.ACCEPTED)
    rec = _sample_recommendation(recommended_drug_class=None)
    draft = build_medication_order_draft(rec, decision, rec.review_panel)
    assert draft is None


def test_order_draft_produced_when_accepted():
    decision = PhysicianDecision(recommendation_id="R1", status=PhysicianDecisionStatus.ACCEPTED)
    rec = _sample_recommendation()
    draft = build_medication_order_draft(rec, decision, rec.review_panel)
    assert draft is not None
    assert draft.drug_class == "SGLT2_INHIBITOR"
    assert draft.physician_decision_status == "ACCEPTED"


def test_order_draft_uses_modified_action_text_when_modified():
    decision = PhysicianDecision(
        recommendation_id="R1", status=PhysicianDecisionStatus.MODIFIED, modified_action_text="改開 GLP-1 RA 而非 SGLT2i"
    )
    rec = _sample_recommendation()
    draft = build_medication_order_draft(rec, decision, rec.review_panel)
    assert draft is not None
    assert draft.order_text == "改開 GLP-1 RA 而非 SGLT2i"


def test_order_draft_raises_on_mismatched_recommendation_and_decision_ids():
    """回歸測試（Codex 審閱發現）：`decision`/`recommendation` 是兩個獨立
    參數，呼叫端傳錯配對（例如把別的建議的 ACCEPTED 決定誤配給這筆建議）
    必須顯式失敗，不可悄悄產生一份授權錯誤藥物 class 的醫令草稿。"""
    import pytest

    from dm_care_pipeline.physician_decision import DecisionValidationError

    mismatched_decision = PhysicianDecision(recommendation_id="SOME_OTHER_RECOMMENDATION", status=PhysicianDecisionStatus.ACCEPTED)
    rec = _sample_recommendation()  # recommendation_id="R1"
    with pytest.raises(DecisionValidationError):
        build_medication_order_draft(rec, mismatched_decision, rec.review_panel)
