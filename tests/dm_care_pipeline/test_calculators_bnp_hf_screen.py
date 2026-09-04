"""`calculators/bnp_hf_screen.py`（BNP/NT-proBNP HF screening）測試。"""

from __future__ import annotations

from datetime import date

from dm_care_pipeline.calculators.base import CalculatorExecutionStatus
from dm_care_pipeline.calculators.bnp_hf_screen import (
    NatriureticPeptideHFScreenCalculator,
    NatriureticPeptideInputs,
)
from dm_care_pipeline.clinical_data_object import ClinicalStatus

AS_OF = date(2024, 6, 1)
calc = NatriureticPeptideHFScreenCalculator()


def test_both_missing_is_insufficient_data():
    result = calc.compute(NatriureticPeptideInputs(patient_id="P1", as_of=AS_OF))
    assert result.execution_status == CalculatorExecutionStatus.INSUFFICIENT_DATA


def test_bnp_at_threshold_is_abnormal():
    result = calc.compute(NatriureticPeptideInputs(patient_id="P1", as_of=AS_OF, bnp_pg_ml=50.0))
    assert result.clinical_status == ClinicalStatus.SUSPECTED
    assert result.action is not None


def test_bnp_below_threshold_is_normal():
    result = calc.compute(NatriureticPeptideInputs(patient_id="P1", as_of=AS_OF, bnp_pg_ml=49.9))
    assert result.clinical_status is None


def test_nt_probnp_at_threshold_is_abnormal():
    result = calc.compute(NatriureticPeptideInputs(patient_id="P1", as_of=AS_OF, nt_probnp_pg_ml=125.0))
    assert result.clinical_status == ClinicalStatus.SUSPECTED


def test_nt_probnp_below_threshold_is_normal():
    result = calc.compute(NatriureticPeptideInputs(patient_id="P1", as_of=AS_OF, nt_probnp_pg_ml=124.9))
    assert result.clinical_status is None


def test_modifiers_are_reported_but_do_not_change_threshold():
    result = calc.compute(
        NatriureticPeptideInputs(
            patient_id="P1", as_of=AS_OF, bnp_pg_ml=10.0, has_ckd=True, has_atrial_fibrillation=True
        )
    )
    assert result.clinical_status is None  # 門檻不受 modifier 影響
    assert any("CKD" in w for w in result.warnings)
