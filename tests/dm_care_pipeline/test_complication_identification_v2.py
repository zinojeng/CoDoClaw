"""
`complication_identification.py` v2 擴充測試：新增 ICD-10 前綴類別
（HEART_FAILURE/MASLD_MASH/OBESITY/FOOT_ULCER_HISTORY/AMPUTATION_HISTORY）
與 `COMPLICATION_CATEGORY_TO_DOMAIN` 映射。既有 6 類（v1）行為見
`tests/test_care_pipeline.py`，此檔案不重複覆蓋。
"""

from __future__ import annotations

from datetime import date

from dm_eligibility.models import DiagnosisRecord, Encounter, PatientEnrollmentState

from dm_care_pipeline.clinical_data_object import ClinicalDomain
from dm_care_pipeline.complication_identification import (
    COMPLICATION_CATEGORY_TO_DOMAIN,
    COMPLICATION_ICD10_PREFIXES,
    identify_complications,
)
from dm_care_pipeline.data_integration import build_patient_clinical_profile

AS_OF = date(2024, 6, 1)


def encounter(icd10: str) -> Encounter:
    return Encounter(
        encounter_id=f"E-{icd10}",
        visit_date=AS_OF,
        physician_id="DOC1",
        diagnoses=(DiagnosisRecord(icd10, is_primary=True),),
    )


def profile_with_diagnoses(*icd10_codes: str):
    state = PatientEnrollmentState(
        patient_id="P1", as_of_date=AS_OF, encounters=[encounter(c) for c in icd10_codes]
    )
    return build_patient_clinical_profile(state)


def test_category_to_domain_covers_every_icd10_prefix_category():
    assert set(COMPLICATION_CATEGORY_TO_DOMAIN.keys()) == set(COMPLICATION_ICD10_PREFIXES.keys())


def test_heart_failure_code_identified_and_mapped_to_heart_failure_domain():
    profile = profile_with_diagnoses("I50.9")
    report = identify_complications(profile)
    categories = {f.category for f in report.findings}
    assert "HEART_FAILURE" in categories
    assert COMPLICATION_CATEGORY_TO_DOMAIN["HEART_FAILURE"] == ClinicalDomain.HEART_FAILURE


def test_masld_mash_code_identified_and_mapped_to_liver_domain():
    profile = profile_with_diagnoses("K76.0")
    report = identify_complications(profile)
    categories = {f.category for f in report.findings}
    assert "MASLD_MASH" in categories
    assert COMPLICATION_CATEGORY_TO_DOMAIN["MASLD_MASH"] == ClinicalDomain.LIVER


def test_obesity_code_identified_and_mapped_to_weight_obesity_domain():
    profile = profile_with_diagnoses("E66.01")
    report = identify_complications(profile)
    categories = {f.category for f in report.findings}
    assert "OBESITY" in categories
    assert COMPLICATION_CATEGORY_TO_DOMAIN["OBESITY"] == ClinicalDomain.WEIGHT_OBESITY


def test_foot_ulcer_and_amputation_history_both_map_to_foot_domain():
    profile = profile_with_diagnoses("L97.501", "Z89.512")
    report = identify_complications(profile)
    categories = {f.category for f in report.findings}
    assert {"FOOT_ULCER_HISTORY", "AMPUTATION_HISTORY"} <= categories
    assert COMPLICATION_CATEGORY_TO_DOMAIN["FOOT_ULCER_HISTORY"] == ClinicalDomain.FOOT
    assert COMPLICATION_CATEGORY_TO_DOMAIN["AMPUTATION_HISTORY"] == ClinicalDomain.FOOT


def test_existing_v1_categories_still_map_correctly():
    assert COMPLICATION_CATEGORY_TO_DOMAIN["NEPHROPATHY"] == ClinicalDomain.KIDNEY
    assert COMPLICATION_CATEGORY_TO_DOMAIN["RETINOPATHY"] == ClinicalDomain.EYE
    assert COMPLICATION_CATEGORY_TO_DOMAIN["NEUROPATHY"] == ClinicalDomain.NEUROPATHY
    assert COMPLICATION_CATEGORY_TO_DOMAIN["PVD"] == ClinicalDomain.PAD
    assert COMPLICATION_CATEGORY_TO_DOMAIN["CVD"] == ClinicalDomain.ASCVD
    assert COMPLICATION_CATEGORY_TO_DOMAIN["CEREBROVASCULAR"] == ClinicalDomain.CEREBROVASCULAR


def test_unrelated_diagnosis_does_not_trigger_new_categories():
    profile = profile_with_diagnoses("J45.909")  # asthma，與新增五類皆無關
    report = identify_complications(profile)
    categories = {f.category for f in report.findings}
    assert categories.isdisjoint({"HEART_FAILURE", "MASLD_MASH", "OBESITY", "FOOT_ULCER_HISTORY", "AMPUTATION_HISTORY"})
