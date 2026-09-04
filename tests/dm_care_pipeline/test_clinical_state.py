"""
`clinical_state.py`（Layer 2）測試。

涵蓋情境：
- 併發症 → CONFIRMED finding，domain 正確映射，NEPHROPATHY 帶 ckd_stage
- Care Gap → CARE_GAP finding，domain 依 lab item 對照
- Tier A COMPUTED + clinical_status → 直接採用該 clinical_status
- Tier B REQUIRES_EXTERNAL_VALIDATED_MODEL → CARE_GAP + is_placeholder=True
- RuleBasedRiskCalculator contributions → CARE_GAP + is_placeholder=True，
  掛在 config.placeholder_risk_finding_domain
- domain_summaries：每個 ClinicalDomain 皆有輸出；GRAY/GREEN/RED/YELLOW 邏輯
- PatientClinicalState 的 confirmed()/suspected()/high_risk()/care_gaps()/
  by_domain()/get()
- 自訂 ClinicalStatusResolver 可完全取代預設邏輯
"""

from __future__ import annotations

from datetime import date, timedelta

from dm_eligibility.models import CKDAssessment, DiagnosisRecord, Encounter, LabResult, PatientEnrollmentState

from dm_care_pipeline.calculators.base import (
    CalculatorExecutionStatus,
    CalculatorResult,
    CalculatorTier,
)
from dm_care_pipeline.calculators.ckd_ga import CKDGACalculator, CKDGAInputs
from dm_care_pipeline.calculators.tier_b.watch_dm import WatchDmCalculator, WatchDmInputs
from dm_care_pipeline.clinical_data_layer import ClinicalDataSourceRegistry, SourceSystemStatus
from dm_care_pipeline.clinical_data_object import ClinicalDomain, ClinicalFinding, ClinicalStatus
from dm_care_pipeline.clinical_state import (
    ClinicalStateConfig,
    ClinicalStatusResolver,
    TrafficLight,
    derive_clinical_state,
)
from dm_care_pipeline.complication_identification import identify_complications
from dm_care_pipeline.data_integration import build_patient_clinical_profile
from dm_care_pipeline.risk import assess_risk
from dm_care_pipeline.trend_analysis import analyze_clinical_trends
from dm_care_pipeline.care_gap import assess_care_gaps

AS_OF = date(2024, 6, 1)


def dm_encounter(visit_date: date, icd10: str = "E11.21") -> Encounter:
    return Encounter(
        encounter_id=f"E-{visit_date.isoformat()}-{icd10}",
        visit_date=visit_date,
        physician_id="DOC1",
        diagnoses=(DiagnosisRecord(icd10, is_primary=True),),
    )


def build_full_state_and_reports(state: PatientEnrollmentState, calculator_results=None):
    profile = build_patient_clinical_profile(state)
    trend_report = analyze_clinical_trends(profile)
    complication_report = identify_complications(profile)
    care_gap_report = assess_care_gaps(profile, codes_in_scope=["P1407C"], include_quality_monitoring=False)
    risk_result = assess_risk(profile, trend_report, complication_report)
    clinical_state = derive_clinical_state(
        profile, complication_report, care_gap_report, risk_result, calculator_results=calculator_results
    )
    return profile, clinical_state


# ---------------------------------------------------------------------------
# 併發症 → CONFIRMED finding
# ---------------------------------------------------------------------------


def test_complication_becomes_confirmed_finding_with_correct_domain():
    state = PatientEnrollmentState(
        patient_id="P1",
        as_of_date=AS_OF,
        encounters=[dm_encounter(AS_OF, icd10="E11.21")],
        ckd_assessments=[CKDAssessment(AS_OF, egfr=50.0, upcr=200.0, is_diabetic=True)],
    )
    _, clinical_state = build_full_state_and_reports(state)
    kidney_findings = clinical_state.by_domain(ClinicalDomain.KIDNEY)
    confirmed_kidney = [f for f in kidney_findings if f.status == ClinicalStatus.CONFIRMED]
    assert len(confirmed_kidney) == 1
    assert confirmed_kidney[0].severity == "3a"
    assert confirmed_kidney[0].is_placeholder is False


def test_complication_finding_in_confirmed_accessor():
    state = PatientEnrollmentState(
        patient_id="P1", as_of_date=AS_OF, encounters=[dm_encounter(AS_OF, icd10="E11.51")]
    )
    _, clinical_state = build_full_state_and_reports(state)
    assert any(f.domain == ClinicalDomain.PAD for f in clinical_state.confirmed())


# ---------------------------------------------------------------------------
# Care Gap → CARE_GAP finding
# ---------------------------------------------------------------------------


def test_care_gap_becomes_care_gap_finding_with_domain_from_lab_item():
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, encounters=[])
    _, clinical_state = build_full_state_and_reports(state)
    eye_care_gaps = [f for f in clinical_state.by_domain(ClinicalDomain.EYE) if f.status == ClinicalStatus.CARE_GAP]
    # P1407C 要求 23501C/23502C 眼底檢查，未提供任何檢驗結果 → 應命中 care gap
    assert len(eye_care_gaps) == 1


def test_satisfied_care_gap_item_produces_no_finding():
    state = PatientEnrollmentState(
        patient_id="P1",
        as_of_date=AS_OF,
        encounters=[],
        lab_results=[LabResult("23501C", AS_OF - timedelta(days=10), value=1.0)],
    )
    _, clinical_state = build_full_state_and_reports(state)
    eye_findings = clinical_state.by_domain(ClinicalDomain.EYE)
    assert not any(f.status == ClinicalStatus.CARE_GAP and "眼睛" in f.condition for f in eye_findings)


# ---------------------------------------------------------------------------
# Calculator 結果整合
# ---------------------------------------------------------------------------


def test_tier_a_computed_clinical_status_is_adopted_directly():
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, encounters=[])
    ckd_result = CKDGACalculator().compute(CKDGAInputs(patient_id="P1", as_of=AS_OF, egfr=50.0, uacr=100.0))
    assert ckd_result.clinical_status == ClinicalStatus.SUSPECTED
    _, clinical_state = build_full_state_and_reports(state, calculator_results={"KDIGO_GA": ckd_result})
    kidney_findings = clinical_state.by_domain(ClinicalDomain.KIDNEY)
    matched = [f for f in kidney_findings if f.calculator == "KDIGO_GA"]
    assert len(matched) == 1
    assert matched[0].status == ClinicalStatus.SUSPECTED
    assert matched[0].is_placeholder is False


def test_tier_b_result_becomes_placeholder_care_gap():
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, encounters=[])
    watch_dm_result = WatchDmCalculator().compute(WatchDmInputs(patient_id="P1", as_of=AS_OF))
    assert watch_dm_result.execution_status == CalculatorExecutionStatus.REQUIRES_EXTERNAL_VALIDATED_MODEL
    _, clinical_state = build_full_state_and_reports(state, calculator_results={"WATCH_DM": watch_dm_result})
    hf_findings = clinical_state.by_domain(ClinicalDomain.HEART_FAILURE)
    matched = [f for f in hf_findings if f.calculator == "WATCH_DM"]
    assert len(matched) == 1
    assert matched[0].status == ClinicalStatus.CARE_GAP
    assert matched[0].severity == "pending_local_validation"
    assert matched[0].is_placeholder is True
    assert matched[0].model_provenance is not None


def test_unmapped_calculator_id_is_skipped_with_warning():
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, encounters=[])
    fake_result = CalculatorResult(
        calculator_id="NOT_REGISTERED",
        calculator_version="v1.0",
        tier=CalculatorTier.A,
        patient_id="P1",
        computed_at=AS_OF,
        execution_status=CalculatorExecutionStatus.COMPUTED,
        result_values={"x": 1},
    )
    _, clinical_state = build_full_state_and_reports(state, calculator_results={"NOT_REGISTERED": fake_result})
    assert not any(f.calculator == "NOT_REGISTERED" for f in clinical_state.findings)
    assert any("NOT_REGISTERED" in w for w in clinical_state.warnings)


# ---------------------------------------------------------------------------
# Risk placeholder findings
# ---------------------------------------------------------------------------


def test_risk_contributions_all_become_placeholder_care_gap_findings():
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, encounters=[])
    _, clinical_state = build_full_state_and_reports(state)
    risk_findings = [f for f in clinical_state.findings if ":RISK:" in f.finding_id]
    assert len(risk_findings) > 0
    for f in risk_findings:
        assert f.is_placeholder is True
        assert f.status == ClinicalStatus.CARE_GAP
        assert f.domain == ClinicalStateConfig().placeholder_risk_finding_domain
        assert "非已驗證公式" in f.condition


# ---------------------------------------------------------------------------
# domain_summaries / TrafficLight
# ---------------------------------------------------------------------------


def test_domain_summaries_present_for_every_clinical_domain():
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, encounters=[])
    _, clinical_state = build_full_state_and_reports(state)
    assert set(clinical_state.domain_summaries.keys()) == set(ClinicalDomain)


def test_domain_with_confirmed_finding_is_red():
    state = PatientEnrollmentState(
        patient_id="P1", as_of_date=AS_OF, encounters=[dm_encounter(AS_OF, icd10="I25.10")]
    )
    _, clinical_state = build_full_state_and_reports(state)
    assert clinical_state.domain_summaries[ClinicalDomain.ASCVD].traffic_light == TrafficLight.RED


def test_domain_relying_on_layer1_source_not_integrated_is_gray():
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, encounters=[])
    profile = build_patient_clinical_profile(state)  # 預設 registry 全 NOT_INTEGRATED
    trend_report = analyze_clinical_trends(profile)
    complication_report = identify_complications(profile)
    care_gap_report = assess_care_gaps(profile, codes_in_scope=[], include_quality_monitoring=False)
    risk_result = assess_risk(profile, trend_report, complication_report)
    clinical_state = derive_clinical_state(profile, complication_report, care_gap_report, risk_result)
    assert clinical_state.domain_summaries[ClinicalDomain.EYE].traffic_light == TrafficLight.GRAY
    assert clinical_state.domain_summaries[ClinicalDomain.HEART_FAILURE].traffic_light == TrafficLight.GRAY


def test_domain_relying_on_layer1_source_integrated_has_data_is_green_without_findings():
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, encounters=[])
    profile = build_patient_clinical_profile(
        state,
        data_source_registry=ClinicalDataSourceRegistry(ophthalmology=SourceSystemStatus.INTEGRATED_HAS_DATA),
    )
    trend_report = analyze_clinical_trends(profile)
    complication_report = identify_complications(profile)
    care_gap_report = assess_care_gaps(profile, codes_in_scope=[], include_quality_monitoring=False)
    risk_result = assess_risk(profile, trend_report, complication_report)
    clinical_state = derive_clinical_state(profile, complication_report, care_gap_report, risk_result)
    assert clinical_state.domain_summaries[ClinicalDomain.EYE].traffic_light == TrafficLight.GREEN


def test_core_part1_domain_green_when_encounters_exist_without_gray_source_dependency():
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, encounters=[dm_encounter(AS_OF)])
    _, clinical_state = build_full_state_and_reports(state)
    # KIDNEY 不依賴 Layer1 擴充來源，有就診紀錄即視為已評估
    assert clinical_state.domain_summaries[ClinicalDomain.KIDNEY].traffic_light in (TrafficLight.GREEN, TrafficLight.RED)
    assert clinical_state.domain_summaries[ClinicalDomain.KIDNEY].traffic_light != TrafficLight.GRAY


def test_core_part1_domain_gray_when_absolutely_no_data():
    # codes_in_scope=[] 且無就診/檢驗紀錄：不產生任何 care gap/併發症 finding，
    # KIDNEY 純粹落回「完全無資料」分支。
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, encounters=[], lab_results=[])
    profile = build_patient_clinical_profile(state)
    trend_report = analyze_clinical_trends(profile)
    complication_report = identify_complications(profile)
    care_gap_report = assess_care_gaps(profile, codes_in_scope=[], include_quality_monitoring=False)
    risk_result = assess_risk(profile, trend_report, complication_report)
    clinical_state = derive_clinical_state(profile, complication_report, care_gap_report, risk_result)
    assert clinical_state.domain_summaries[ClinicalDomain.KIDNEY].traffic_light == TrafficLight.GRAY


def _build_state_no_care_gap_scope(state: PatientEnrollmentState):
    """比照 test_core_part1_domain_gray_when_absolutely_no_data()：
    codes_in_scope=[] 避免 P1407C 的必要檢驗缺漏（黃燈 CARE_GAP finding）
    蓋過本測試真正要驗證的「無 finding 時的 fallback 判準」。"""
    profile = build_patient_clinical_profile(state)
    trend_report = analyze_clinical_trends(profile)
    complication_report = identify_complications(profile)
    care_gap_report = assess_care_gaps(profile, codes_in_scope=[], include_quality_monitoring=False)
    risk_result = assess_risk(profile, trend_report, complication_report)
    return derive_clinical_state(profile, complication_report, care_gap_report, risk_result)


def test_kidney_domain_gray_for_unrelated_encounter_without_ckd_data():
    """回歸測試（Codex #23）：與腎臟完全無關的就診（無 CKDAssessment、
    無腎功能檢驗，診斷碼也非腎臟相關）不應讓 KIDNEY domain 顯示綠燈——
    先前只要「有任何一筆就診或檢驗紀錄」就視為已評估，一筆氣喘回診也會
    讓 KIDNEY 顯示「No abnormal finding documented」。"""
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, encounters=[dm_encounter(AS_OF, icd10="J45.9")])
    clinical_state = _build_state_no_care_gap_scope(state)
    assert clinical_state.domain_summaries[ClinicalDomain.KIDNEY].traffic_light == TrafficLight.GRAY


def test_kidney_domain_green_when_ckd_assessment_present():
    """正向對照：真的有 CKDAssessment（eGFR/UACR）時，KIDNEY 才顯示綠燈。"""
    state = PatientEnrollmentState(
        patient_id="P1",
        as_of_date=AS_OF,
        encounters=[dm_encounter(AS_OF, icd10="J45.9")],
        ckd_assessments=[CKDAssessment(AS_OF, egfr=90.0, upcr=10.0, is_diabetic=True)],
    )
    clinical_state = _build_state_no_care_gap_scope(state)
    assert clinical_state.domain_summaries[ClinicalDomain.KIDNEY].traffic_light == TrafficLight.GREEN


def test_glycemic_control_domain_has_relevant_data_requires_hba1c_lab():
    """回歸測試（Codex #23），GLYCEMIC_CONTROL 對照版本：直接單元測試
    `_domain_has_relevant_data()`（而非透過完整 pipeline）——GLYCEMIC_
    CONTROL 在完整 pipeline 中永遠會被 `risk.py` 的 placeholder finding
    蓋成 YELLOW（`ClinicalStateConfig.placeholder_risk_finding_domain`
    預設固定掛在此 domain，與本次修正的 fallback 分支判準是兩件獨立的
    事，故在此改為直接測試判準函式本身，避免被無關的既有行為蓋掉）。"""
    from dm_care_pipeline.clinical_state import _domain_has_relevant_data

    state_without_hba1c = PatientEnrollmentState(
        patient_id="P1", as_of_date=AS_OF, encounters=[dm_encounter(AS_OF, icd10="J45.9")]
    )
    profile_without_hba1c = build_patient_clinical_profile(state_without_hba1c)
    assert _domain_has_relevant_data(ClinicalDomain.GLYCEMIC_CONTROL, profile_without_hba1c) is False

    state_with_hba1c = PatientEnrollmentState(
        patient_id="P1",
        as_of_date=AS_OF,
        encounters=[dm_encounter(AS_OF, icd10="J45.9")],
        lab_results=[LabResult("09006C", AS_OF, value=7.0)],
    )
    profile_with_hba1c = build_patient_clinical_profile(state_with_hba1c)
    assert _domain_has_relevant_data(ClinicalDomain.GLYCEMIC_CONTROL, profile_with_hba1c) is True


def test_ascvd_domain_still_green_for_any_encounter_with_diagnoses():
    """ASCVD/CEREBROVASCULAR 透過 ICD 診斷碼辨識（`identify_complications()`
    掃描全部就診之診斷欄位，診斷碼可能出現在任一次就診，不侷限特定檢驗
    代碼）——與 KIDNEY/GLYCEMIC_CONTROL 需要專屬檢驗資料不同，任一次有
    記錄診斷的就診即視為已評估，維持既有行為（非本次修正範圍）。"""
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, encounters=[dm_encounter(AS_OF, icd10="J45.9")])
    clinical_state = _build_state_no_care_gap_scope(state)
    assert clinical_state.domain_summaries[ClinicalDomain.ASCVD].traffic_light == TrafficLight.GREEN


# ---------------------------------------------------------------------------
# PatientClinicalState accessor 方法
# ---------------------------------------------------------------------------


def test_state_accessors_filter_by_status_and_domain():
    state = PatientEnrollmentState(
        patient_id="P1", as_of_date=AS_OF, encounters=[dm_encounter(AS_OF, icd10="E11.21")]
    )
    _, clinical_state = build_full_state_and_reports(state)
    assert all(f.status == ClinicalStatus.CONFIRMED for f in clinical_state.confirmed())
    assert all(f.status == ClinicalStatus.CARE_GAP for f in clinical_state.care_gaps())
    assert all(f.status == ClinicalStatus.SUSPECTED for f in clinical_state.suspected())
    assert all(f.status == ClinicalStatus.HIGH_RISK for f in clinical_state.high_risk())
    kidney = clinical_state.by_domain(ClinicalDomain.KIDNEY)
    assert all(f.domain == ClinicalDomain.KIDNEY for f in kidney)


def test_get_returns_finding_by_id_or_none():
    state = PatientEnrollmentState(
        patient_id="P1", as_of_date=AS_OF, encounters=[dm_encounter(AS_OF, icd10="E11.21")]
    )
    _, clinical_state = build_full_state_and_reports(state)
    some_finding = clinical_state.findings[0]
    assert clinical_state.get(some_finding.finding_id) is some_finding
    assert clinical_state.get("no-such-id") is None


# ---------------------------------------------------------------------------
# 自訂 resolver
# ---------------------------------------------------------------------------


class _AlwaysEmptyResolver:
    def resolve(self, domain, profile, complication_report, care_gap_report, calculator_results):
        return ()


def test_custom_resolver_replaces_default_logic():
    state = PatientEnrollmentState(
        patient_id="P1", as_of_date=AS_OF, encounters=[dm_encounter(AS_OF, icd10="E11.21")]
    )
    profile = build_patient_clinical_profile(state)
    trend_report = analyze_clinical_trends(profile)
    complication_report = identify_complications(profile)
    care_gap_report = assess_care_gaps(profile, codes_in_scope=[], include_quality_monitoring=False)
    risk_result = assess_risk(profile, trend_report, complication_report)
    clinical_state = derive_clinical_state(
        profile, complication_report, care_gap_report, risk_result, resolver=_AlwaysEmptyResolver()
    )
    # 自訂 resolver 回傳空 tuple，唯一還會出現的 finding 是 risk placeholder
    # （不是 domain-scoped resolver 輸入，見模組設計）
    non_risk_findings = [f for f in clinical_state.findings if ":RISK:" not in f.finding_id]
    assert non_risk_findings == []
    assert any(":RISK:" in f.finding_id for f in clinical_state.findings)


def test_finding_id_deduplicated_on_collision():
    from dm_care_pipeline.clinical_state import _finding_id

    used: set[str] = set()
    id1 = _finding_id(used, ClinicalDomain.KIDNEY, "CKD", "P1", AS_OF)
    id2 = _finding_id(used, ClinicalDomain.KIDNEY, "CKD", "P1", AS_OF)
    assert id1 != id2
    assert id2.startswith(id1)
