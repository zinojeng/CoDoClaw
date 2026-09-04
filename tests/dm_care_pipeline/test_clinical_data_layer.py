"""
`clinical_data_layer.py`（Layer1 擴充）+ `pipeline_models.py`/
`data_integration.py` 對應整合點測試。

涵蓋情境：
- SourceSystemStatus 三態、ClinicalDataSourceRegistry.all_not_integrated()
- 各 frozen 容器型別的基本建構
- PatientClinicalProfile v2 新欄位預設值向下相容
- build_patient_clinical_profile() 傳入新欄位後正確組裝、預設 registry 全
  NOT_INTEGRATED 時產生 DataGapFlag(source="clinical_data_layer")
"""

from __future__ import annotations

from datetime import date

from dm_eligibility.models import EligibilityReport, PatientEnrollmentState

from dm_care_pipeline.clinical_data_layer import (
    AdministrativeCareStatus,
    AmputationRecord,
    CardiacImagingFinding,
    ClinicalDataSourceRegistry,
    EncounterUtilizationRecord,
    FootNeuroExam,
    HypoglycemiaEventRecord,
    ImagingStudyRef,
    OphthalmologyFinding,
    ProcedureRecord,
    ReferralRecord,
    SmokingStatus,
    SourceSystemStatus,
    UlcerRecord,
    VascularExam,
    VitalSignObservation,
)
from dm_care_pipeline.data_integration import build_patient_clinical_profile

AS_OF = date(2024, 6, 1)


def make_state(**overrides) -> PatientEnrollmentState:
    defaults = dict(patient_id="P1", as_of_date=AS_OF)
    defaults.update(overrides)
    return PatientEnrollmentState(**defaults)


# ---------------------------------------------------------------------------
# SourceSystemStatus / ClinicalDataSourceRegistry
# ---------------------------------------------------------------------------


def test_source_system_status_has_three_values():
    assert {s.value for s in SourceSystemStatus} == {"not_integrated", "integrated_no_data", "integrated_has_data"}


def test_registry_default_all_not_integrated():
    registry = ClinicalDataSourceRegistry()
    assert registry.all_not_integrated() is True


def test_registry_one_integrated_source_breaks_all_not_integrated():
    registry = ClinicalDataSourceRegistry(ophthalmology=SourceSystemStatus.INTEGRATED_HAS_DATA)
    assert registry.all_not_integrated() is False


def test_registry_integrated_no_data_also_breaks_all_not_integrated():
    registry = ClinicalDataSourceRegistry(admin=SourceSystemStatus.INTEGRATED_NO_DATA)
    assert registry.all_not_integrated() is False


# ---------------------------------------------------------------------------
# 容器型別基本建構
# ---------------------------------------------------------------------------


def test_vital_sign_observation_defaults():
    v = VitalSignObservation(observation_date=AS_OF)
    assert v.smoking_status == SmokingStatus.UNKNOWN
    assert v.source == "HIS"


def test_ophthalmology_finding_requires_method_and_classification():
    f = OphthalmologyFinding(exam_date=AS_OF, method="VeriSee_AI", dr_classification="mild_npdr")
    assert f.dr_classification == "mild_npdr"


def test_foot_neuro_exam_with_ulcer_and_amputation_history():
    exam = FootNeuroExam(
        exam_date=AS_OF,
        monofilament_result_left="abnormal",
        ulcer_history=(UlcerRecord(event_date=AS_OF, resolved=False),),
        amputation_history=(AmputationRecord(event_date=AS_OF, laterality="L"),),
    )
    assert exam.ulcer_history[0].resolved is False
    assert exam.amputation_history[0].laterality == "L"


def test_vascular_exam_with_revascularization_history():
    exam = VascularExam(
        exam_date=AS_OF,
        abi_right=0.5,
        revascularization_history=(
            ProcedureRecord(procedure_code=None, procedure_name="PTA", procedure_date=AS_OF, source="VASCULAR_LAB"),
        ),
    )
    assert exam.revascularization_history[0].procedure_name == "PTA"


def test_cardiac_imaging_finding():
    f = CardiacImagingFinding(study_date=AS_OF, modality="ECHO", lvef_percent=45.0)
    assert f.modality == "ECHO"


def test_imaging_study_ref_default_structured_findings_is_empty_dict():
    ref = ImagingStudyRef(study_date=AS_OF, modality="US", body_region="liver")
    assert ref.structured_findings == {}


def test_hypoglycemia_event_record():
    ev = HypoglycemiaEventRecord(event_date=AS_OF, severity="level2", setting="ed")
    assert ev.severity == "level2"


def test_administrative_care_status_with_pending_referral():
    status = AdministrativeCareStatus(
        pending_referrals=(ReferralRecord(specialty="Ophthalmology", ordered_date=AS_OF, status="ordered"),)
    )
    assert status.pending_referrals[0].specialty == "Ophthalmology"


def test_encounter_utilization_record():
    rec = EncounterUtilizationRecord(encounter_id="E1", visit_date=AS_OF, setting="ed", hypoglycemia_related=True)
    assert rec.setting == "ed"


# ---------------------------------------------------------------------------
# PatientClinicalProfile / build_patient_clinical_profile() 整合
# ---------------------------------------------------------------------------


def test_profile_v2_fields_default_empty_and_backward_compatible():
    state = make_state()
    profile = build_patient_clinical_profile(state)
    assert profile.vital_signs == ()
    assert profile.sex is None
    assert profile.administrative_status is None
    assert isinstance(profile.data_source_registry, ClinicalDataSourceRegistry)


def test_profile_default_registry_all_not_integrated_flags_data_gap():
    state = make_state()
    profile = build_patient_clinical_profile(state)
    gap_sources = {g.source for g in profile.data_gaps}
    assert "clinical_data_layer" in gap_sources


def test_profile_with_partial_registry_does_not_flag_clinical_data_layer_gap():
    state = make_state()
    registry = ClinicalDataSourceRegistry(ophthalmology=SourceSystemStatus.INTEGRATED_HAS_DATA)
    profile = build_patient_clinical_profile(state, data_source_registry=registry)
    gap_sources = {g.source for g in profile.data_gaps}
    assert "clinical_data_layer" not in gap_sources


def test_profile_passes_through_new_keyword_fields():
    state = make_state()
    vitals = (VitalSignObservation(observation_date=AS_OF, systolic_bp=130),)
    profile = build_patient_clinical_profile(state, vital_signs=vitals, sex="female")
    assert profile.vital_signs == vitals
    assert profile.sex == "female"


# ---------------------------------------------------------------------------
# 回歸測試：eligibility_report 跨病人/過期資料防護（鐵律5：不靜默混用）
# ---------------------------------------------------------------------------


def test_profile_drops_eligibility_report_for_a_different_patient():
    """規格精神：EligibilityReport 屬於另一位病人時，絕不能靜默流向 care_gap/
    guideline_recommendation/followup——先前 data_integration.py 只比對
    as_of_date，從未檢查 patient_id，會讓另一病人的收案資格報告驅動本次
    評估的 care gap 與「今日已申報」判斷。"""
    state = make_state(patient_id="P1")
    other_patient_report = EligibilityReport(patient_id="P999", as_of_date=AS_OF)

    profile = build_patient_clinical_profile(state, eligibility_report=other_patient_report)

    assert profile.eligibility_report is None
    assert profile.eligibility_report_patient_id_mismatch is True
    gap_sources = {g.source for g in profile.data_gaps}
    assert "eligibility_report" in gap_sources


def test_profile_drops_eligibility_report_with_stale_as_of_date():
    """回歸測試：`eligibility_report_as_of_mismatch` 先前只被寫入、從未被
    下游讀取，過期/未來的 EligibilityReport 仍會被當成本次評估的收案資格
    現況使用。現在應直接捨棄，下游視同未提供。"""
    state = make_state(patient_id="P1", as_of_date=AS_OF)
    stale_report = EligibilityReport(patient_id="P1", as_of_date=date(2024, 1, 1))

    profile = build_patient_clinical_profile(state, eligibility_report=stale_report)

    assert profile.eligibility_report is None
    assert profile.eligibility_report_as_of_mismatch is True
    gap_sources = {g.source for g in profile.data_gaps}
    assert "eligibility_report" in gap_sources


def test_profile_keeps_matching_eligibility_report():
    """正向對照：patient_id 與 as_of_date 皆相符時，報告應正常流入 profile。"""
    state = make_state(patient_id="P1", as_of_date=AS_OF)
    matching_report = EligibilityReport(patient_id="P1", as_of_date=AS_OF)

    profile = build_patient_clinical_profile(state, eligibility_report=matching_report)

    assert profile.eligibility_report is matching_report
    assert profile.eligibility_report_patient_id_mismatch is False
    assert profile.eligibility_report_as_of_mismatch is False
