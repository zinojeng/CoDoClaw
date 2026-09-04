"""
`guideline_recommendation.py` v2 擴充測試：`GuidelineSource`/
`GUIDELINE_LIBRARY`、`RecommendationRule`/`GuidelineRecommendation` 新增
欄位、`GuidelineRecommendationInput.calculator_results`、6 條新 Tier A 規則、
4 條新 Tier B 資訊揭露規則、`related_finding_id_matcher` 串接。既有 4 條 v1
規則行為見 `tests/test_care_pipeline.py`，此檔案不重複覆蓋。
"""

from __future__ import annotations

from datetime import date

from dm_eligibility import rules_p14, rules_p7
from dm_eligibility.models import PatientEnrollmentState

from dm_care_pipeline.calculators.base import CalculatorExecutionStatus, CalculatorResult, CalculatorTier
from dm_care_pipeline.calculators.ckd_ga import CKDGACalculator, CKDGAInputs
from dm_care_pipeline.calculators.fib4 import FIB4Calculator, FIB4Inputs
from dm_care_pipeline.calculators.iwgdf_foot import IWGDFFootInputs, IWGDFFootRiskCalculator
from dm_care_pipeline.calculators.tier_b.watch_dm import WatchDmCalculator, WatchDmInputs
from dm_care_pipeline.care_gap import CareGapItem
from dm_care_pipeline.clinical_data_object import ClinicalStatus
from dm_care_pipeline.complication_identification import identify_complications
from dm_care_pipeline.data_integration import build_patient_clinical_profile
from dm_care_pipeline.guideline_recommendation import (
    GUIDELINE_LIBRARY,
    GuidelineRecommendationEngine,
    GuidelineRecommendationInput,
    GuidelineSource,
    RecommendationPriority,
    build_guideline_input,
)
from dm_care_pipeline.risk import assess_risk
from dm_care_pipeline.trend_analysis import analyze_clinical_trends

AS_OF = date(2024, 6, 1)


def _base_input(**overrides) -> GuidelineRecommendationInput:
    defaults = dict(patient_id="P1", as_of_date=AS_OF)
    defaults.update(overrides)
    return GuidelineRecommendationInput(**defaults)


# ---------------------------------------------------------------------------
# GUIDELINE_LIBRARY
# ---------------------------------------------------------------------------


def test_guideline_library_has_eight_entries():
    assert len(GUIDELINE_LIBRARY) == 8
    assert set(GUIDELINE_LIBRARY.keys()) == {
        "ADA_SOC_2026",
        "Taiwan_DM_Guideline_2022",
        "Taiwan_DKD_2024",
        "KDIGO",
        "AHA_ACC",
        "IWGDF_2023",
        "Taiwan_NHI_DM_P4P_2026",
        "Taiwan_NHI_CKD_P4P_2026",
    }
    for key, source in GUIDELINE_LIBRARY.items():
        assert isinstance(source, GuidelineSource)
        assert source.guideline_id == key


# ---------------------------------------------------------------------------
# build_guideline_input() 向下相容 + v2 擴充
# ---------------------------------------------------------------------------


def test_build_guideline_input_backward_compatible_without_v2_kwargs():
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, encounters=[])
    profile = build_patient_clinical_profile(state)
    trend_report = analyze_clinical_trends(profile)
    complication_report = identify_complications(profile)
    risk_result = assess_risk(profile, trend_report, complication_report)
    from dm_care_pipeline.care_gap import assess_care_gaps

    care_gap_report = assess_care_gaps(profile, codes_in_scope=[], include_quality_monitoring=False)
    inp = build_guideline_input(profile, trend_report, complication_report, risk_result, care_gap_report)
    assert inp.clinical_state is None
    assert inp.calculator_results == {}


def test_build_guideline_input_accepts_calculator_results():
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, encounters=[])
    profile = build_patient_clinical_profile(state)
    trend_report = analyze_clinical_trends(profile)
    complication_report = identify_complications(profile)
    risk_result = assess_risk(profile, trend_report, complication_report)
    from dm_care_pipeline.care_gap import assess_care_gaps

    care_gap_report = assess_care_gaps(profile, codes_in_scope=[], include_quality_monitoring=False)
    fib4_result = FIB4Calculator().compute(
        FIB4Inputs(patient_id="P1", as_of=AS_OF, age_years=50, ast_u_l=100, alt_u_l=20, platelet_10e9_l=100)
    )
    inp = build_guideline_input(
        profile, trend_report, complication_report, risk_result, care_gap_report, calculator_results={"FIB4": fib4_result}
    )
    assert inp.calculator_results["FIB4"] is fib4_result


# ---------------------------------------------------------------------------
# v2 Tier A 規則
# ---------------------------------------------------------------------------


def test_kdigo_ga_severity_display_triggers_only_when_abnormal():
    normal = CKDGACalculator().compute(CKDGAInputs(patient_id="P1", as_of=AS_OF, egfr=95.0, uacr=10.0))
    abnormal = CKDGACalculator().compute(CKDGAInputs(patient_id="P1", as_of=AS_OF, egfr=50.0, uacr=100.0))

    report_normal = GuidelineRecommendationEngine().build(_base_input(calculator_results={"KDIGO_GA": normal}))
    report_abnormal = GuidelineRecommendationEngine().build(_base_input(calculator_results={"KDIGO_GA": abnormal}))

    assert not any(r.rule_id == "KDIGO_GA_SEVERITY_DISPLAY" for r in report_normal.recommendations)
    hit = next(r for r in report_abnormal.recommendations if r.rule_id == "KDIGO_GA_SEVERITY_DISPLAY")
    assert hit.guideline_id == "KDIGO"
    assert hit.alert_level == "information"
    assert hit.trigger_grounded_in_spec is True


def test_fib4_secondary_assessment_triggers_on_suspected():
    elevated = FIB4Calculator().compute(
        FIB4Inputs(patient_id="P1", as_of=AS_OF, age_years=50, ast_u_l=100, alt_u_l=20, platelet_10e9_l=100)
    )
    report = GuidelineRecommendationEngine().build(_base_input(calculator_results={"FIB4": elevated}))
    hit = next(r for r in report.recommendations if r.rule_id == "FIB4_SECONDARY_ASSESSMENT")
    assert "FibroScan" in hit.rationale or "VCTE" in hit.rationale


def test_fib4_secondary_assessment_silent_when_normal():
    normal = FIB4Calculator().compute(
        FIB4Inputs(patient_id="P1", as_of=AS_OF, age_years=50, ast_u_l=20, alt_u_l=40, platelet_10e9_l=300)
    )
    report = GuidelineRecommendationEngine().build(_base_input(calculator_results={"FIB4": normal}))
    assert not any(r.rule_id == "FIB4_SECONDARY_ASSESSMENT" for r in report.recommendations)


def test_iwgdf_foot_frequency_reminder_triggers_on_any_computed_result():
    result = IWGDFFootRiskCalculator().compute(
        IWGDFFootInputs(patient_id="P1", as_of=AS_OF, lops_present=False, pad_present=False)
    )
    report = GuidelineRecommendationEngine().build(_base_input(calculator_results={"IWGDF_FOOT_RISK": result}))
    assert any(r.rule_id == "IWGDF_FOOT_FREQUENCY_REMINDER" for r in report.recommendations)


def test_nhi_ckd_p4p_lab_gap_matches_p7_lab_item_codes():
    p7_req = rules_p7.P7001_LAB_REQUIREMENTS_BY_CLAIM_NUMBER[1][0]  # 09005C B.S.（P700101）
    item = CareGapItem(
        requirement=p7_req,
        satisfied=False,
        most_recent_within_window=None,
        most_recent_ever=None,
        days_since_last=None,
        source_codes=p7_req.alternatives,
        spec_reference="P7 spec (d)",
        owning_codes=("P7001C",),
    )
    report = GuidelineRecommendationEngine().build(_base_input(care_gaps=(item,)))
    hit = next(r for r in report.recommendations if r.rule_id == "NHI_CKD_P4P_LAB_GAP")
    assert hit.guideline_id == "Taiwan_NHI_CKD_P4P_2026"


def test_nhi_ckd_p4p_lab_gap_silent_for_p14_item_despite_overlapping_lab_code():
    """回歸測試（Codex #6）：09006C(HbA1c) 同時是 P1408C 與 P7001C 的必要
    檢驗項目，先前用 source_codes 與 P7 檢驗代碼集合比對，會把純 P1408C
    的缺漏誤標成 P7 缺漏。現在必須依 owning_codes 判斷——這筆缺漏明確只
    來自 P1408C 的登記，不應觸發 NHI_CKD_P4P_LAB_GAP。"""
    p14_req = rules_p14.P1408_LAB_REQUIREMENTS_BASE[0]  # 09006C HbA1c（P7001C 也需要同一代碼）
    item = CareGapItem(
        requirement=p14_req,
        satisfied=False,
        most_recent_within_window=None,
        most_recent_ever=None,
        days_since_last=None,
        source_codes=p14_req.alternatives,
        spec_reference="P14 spec (b) B.3",
        owning_codes=("P1408C",),
    )
    report = GuidelineRecommendationEngine().build(_base_input(care_gaps=(item,)))
    assert not any(r.rule_id == "NHI_CKD_P4P_LAB_GAP" for r in report.recommendations)


def test_nhi_ckd_p4p_lab_gap_silent_for_unrelated_care_gap():
    from dm_eligibility.models import LabRequirement

    unrelated_req = LabRequirement(("99999X",), 40, "與P7無關的檢驗")
    item = CareGapItem(
        requirement=unrelated_req,
        satisfied=False,
        most_recent_within_window=None,
        most_recent_ever=None,
        days_since_last=None,
        source_codes=unrelated_req.alternatives,
        spec_reference="不明",
    )
    report = GuidelineRecommendationEngine().build(_base_input(care_gaps=(item,)))
    assert not any(r.rule_id == "NHI_CKD_P4P_LAB_GAP" for r in report.recommendations)


# ---------------------------------------------------------------------------
# v2 Tier B 資訊揭露規則
# ---------------------------------------------------------------------------


def test_watch_dm_info_triggers_when_requires_external_model():
    result = WatchDmCalculator().compute(WatchDmInputs(patient_id="P1", as_of=AS_OF))
    assert result.execution_status == CalculatorExecutionStatus.REQUIRES_EXTERNAL_VALIDATED_MODEL
    report = GuidelineRecommendationEngine().build(_base_input(calculator_results={"WATCH_DM": result}))
    hit = next(r for r in report.recommendations if r.rule_id == "WATCH_DM_INFO")
    assert hit.alert_level == "information"
    assert hit.evidence_level == "risk_communication_only_pending_local_validation"
    # 文字禁止帶百分比門檻式建議
    assert "%" not in hit.rationale


def test_tier_b_info_rules_silent_when_calculator_not_provided():
    report = GuidelineRecommendationEngine().build(_base_input())
    tier_b_info_ids = {"WATCH_DM_INFO", "PREVENT_INFO", "KARTER_INFO", "KFRE_INFO"}
    assert not any(r.rule_id in tier_b_info_ids for r in report.recommendations)


def test_all_v2_tier_b_info_rules_use_information_alert_level():
    rules_and_ids = {
        "WATCH_DM_INFO": WatchDmCalculator().compute(WatchDmInputs(patient_id="P1", as_of=AS_OF)),
    }
    report = GuidelineRecommendationEngine().build(
        _base_input(calculator_results={"WATCH_DM": rules_and_ids["WATCH_DM_INFO"]})
    )
    for rec in report.recommendations:
        if rec.rule_id.endswith("_INFO"):
            assert rec.alert_level == "information"


# ---------------------------------------------------------------------------
# related_finding_id_matcher / 既有規則向下相容
# ---------------------------------------------------------------------------


def test_v1_rules_default_new_fields_unchanged_behavior():
    from dm_care_pipeline.trend_analysis import MarkerTrend, QualityMetricTier, TrendDirection

    mt = MarkerTrend(
        marker_name="HBA1C",
        item_code_matched="09006C",
        data_points=[(AS_OF, 9.5)],
        direction=TrendDirection.STABLE,
        is_consecutively_worsening=False,
        slope_per_year=None,
        latest_value=9.5,
        latest_result_date=AS_OF,
        control_tier=QualityMetricTier.POOR,
        method_used="single_point",
    )
    report = GuidelineRecommendationEngine().build(_base_input(marker_trends=(mt,)))
    hit = next(r for r in report.recommendations if r.rule_id == "HBA1C_POOR_NO_RECENT_TRACKING")
    # v1 既有規則未指定 alert_level/guideline_id，應維持預設值，行為不變
    assert hit.alert_level == "clinical_attention"
    assert hit.guideline_id is None
    assert hit.related_finding_id is None
    assert hit.priority == RecommendationPriority.PRIORITY


def test_related_finding_id_matcher_populates_field_when_provided():
    from dm_care_pipeline.guideline_recommendation import RecommendationEvidence, RecommendationRule, EvidenceType

    def always_matches(inp: GuidelineRecommendationInput):
        return [RecommendationEvidence(EvidenceType.CALCULATOR_RESULT, "X", "hit", None)]

    rule = RecommendationRule(
        rule_id="TEST_RULE",
        title_template="test",
        priority=RecommendationPriority.ROUTINE,
        trigger_grounded_in_spec=True,
        action_is_placeholder_content=False,
        spec_reference=None,
        matcher=always_matches,
        related_finding_id_matcher=lambda inp: "KIDNEY:CKD:P1:2024-06-01",
    )
    report = GuidelineRecommendationEngine(rules=[rule]).build(_base_input())
    assert report.recommendations[0].related_finding_id == "KIDNEY:CKD:P1:2024-06-01"


def test_related_finding_id_matcher_exception_does_not_block_recommendation():
    from dm_care_pipeline.guideline_recommendation import RecommendationEvidence, RecommendationRule, EvidenceType

    def always_matches(inp: GuidelineRecommendationInput):
        return [RecommendationEvidence(EvidenceType.CALCULATOR_RESULT, "X", "hit", None)]

    def broken_matcher(inp: GuidelineRecommendationInput):
        raise RuntimeError("boom")

    rule = RecommendationRule(
        rule_id="TEST_RULE_BROKEN",
        title_template="test",
        priority=RecommendationPriority.ROUTINE,
        trigger_grounded_in_spec=True,
        action_is_placeholder_content=False,
        spec_reference=None,
        matcher=always_matches,
        related_finding_id_matcher=broken_matcher,
    )
    report = GuidelineRecommendationEngine(rules=[rule]).build(_base_input())
    assert len(report.recommendations) == 1
    assert report.recommendations[0].related_finding_id is None
    assert any("related_finding_id_matcher" in w for w in report.warnings)
