"""`calculators/ckd_ga.py`（KDIGO G/A 分期）測試。"""

from __future__ import annotations

from datetime import date

from dm_care_pipeline.calculators.base import CalculatorExecutionStatus
from dm_care_pipeline.calculators.ckd_ga import CKDGACalculator, CKDGAInputs
from dm_care_pipeline.clinical_data_object import ClinicalStatus

AS_OF = date(2024, 6, 1)
calc = CKDGACalculator()


def test_both_missing_is_insufficient_data():
    result = calc.compute(CKDGAInputs(patient_id="P1", as_of=AS_OF))
    assert result.execution_status == CalculatorExecutionStatus.INSUFFICIENT_DATA
    assert result.missing_inputs == ("egfr", "uacr")


def test_g1a1_is_normal_no_clinical_status():
    result = calc.compute(CKDGAInputs(patient_id="P1", as_of=AS_OF, egfr=95.0, uacr=10.0))
    assert result.result_summary == "CKD G1A1"
    assert result.clinical_status is None


def test_g3a_a2_without_corroboration_is_suspected():
    result = calc.compute(CKDGAInputs(patient_id="P1", as_of=AS_OF, egfr=50.0, uacr=100.0))
    assert result.result_summary == "CKD G3aA2"
    assert result.clinical_status == ClinicalStatus.SUSPECTED


def test_g3a_a2_with_corroboration_is_confirmed():
    result = calc.compute(
        CKDGAInputs(patient_id="P1", as_of=AS_OF, egfr=50.0, uacr=100.0, corroborating_ckd_diagnosis=True)
    )
    assert result.clinical_status == ClinicalStatus.CONFIRMED


def test_g_stage_boundaries():
    assert calc.compute(CKDGAInputs(patient_id="P1", as_of=AS_OF, egfr=90.0, uacr=10.0)).result_values["g_stage"] == "G1"
    assert calc.compute(CKDGAInputs(patient_id="P1", as_of=AS_OF, egfr=89.9, uacr=10.0)).result_values["g_stage"] == "G2"
    assert calc.compute(CKDGAInputs(patient_id="P1", as_of=AS_OF, egfr=44.9, uacr=10.0)).result_values["g_stage"] == "G3b"
    assert calc.compute(CKDGAInputs(patient_id="P1", as_of=AS_OF, egfr=14.9, uacr=10.0)).result_values["g_stage"] == "G5"


def test_a_stage_boundaries():
    assert calc.compute(CKDGAInputs(patient_id="P1", as_of=AS_OF, egfr=95.0, uacr=29.9)).result_values["a_stage"] == "A1"
    assert calc.compute(CKDGAInputs(patient_id="P1", as_of=AS_OF, egfr=95.0, uacr=300.0)).result_values["a_stage"] == "A2"
    assert calc.compute(CKDGAInputs(patient_id="P1", as_of=AS_OF, egfr=95.0, uacr=300.1)).result_values["a_stage"] == "A3"


def test_only_egfr_provided_abnormal_is_flagged_without_silently_normal():
    result = calc.compute(CKDGAInputs(patient_id="P1", as_of=AS_OF, egfr=40.0))
    assert result.execution_status == CalculatorExecutionStatus.COMPUTED
    assert result.clinical_status == ClinicalStatus.SUSPECTED
    assert "uacr" in result.missing_inputs


def test_only_egfr_provided_normal_gives_no_clinical_status_pending_data():
    result = calc.compute(CKDGAInputs(patient_id="P1", as_of=AS_OF, egfr=95.0))
    assert result.clinical_status is None
