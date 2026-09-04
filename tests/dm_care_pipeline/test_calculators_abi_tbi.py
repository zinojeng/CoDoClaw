"""`calculators/abi_tbi.py`（ABI/TBI PAD screening）測試。"""

from __future__ import annotations

from datetime import date

from dm_care_pipeline.calculators.abi_tbi import ABITBICalculator, ABITBIInputs
from dm_care_pipeline.calculators.base import CalculatorExecutionStatus
from dm_care_pipeline.clinical_data_object import ClinicalStatus

AS_OF = date(2024, 6, 1)
calc = ABITBICalculator()


def test_all_missing_is_insufficient_data():
    result = calc.compute(ABITBIInputs(patient_id="P1", as_of=AS_OF))
    assert result.execution_status == CalculatorExecutionStatus.INSUFFICIENT_DATA


def test_abi_at_or_below_threshold_is_abnormal():
    result = calc.compute(ABITBIInputs(patient_id="P1", as_of=AS_OF, abi_right=0.90, abi_left=1.0))
    assert result.clinical_status == ClinicalStatus.SUSPECTED
    assert result.result_values["right_status"] == "abnormal"


def test_abi_normal_range_is_normal():
    result = calc.compute(ABITBIInputs(patient_id="P1", as_of=AS_OF, abi_right=1.0, abi_left=1.0))
    assert result.clinical_status is None


def test_noncompressible_abi_falls_back_to_tbi_abnormal():
    result = calc.compute(
        ABITBIInputs(patient_id="P1", as_of=AS_OF, abi_right=1.50, tbi_right=0.65, abi_left=1.0)
    )
    assert result.clinical_status == ClinicalStatus.SUSPECTED
    assert result.result_values["right_status"] == "abnormal"


def test_noncompressible_abi_without_tbi_is_insufficient_for_that_side_but_not_abnormal():
    result = calc.compute(ABITBIInputs(patient_id="P1", as_of=AS_OF, abi_right=1.50, abi_left=1.0))
    assert result.execution_status == CalculatorExecutionStatus.COMPUTED
    assert result.clinical_status is None
    assert any("right" in w for w in result.warnings)


def test_only_one_side_abnormal_marks_whole_result_abnormal():
    result = calc.compute(ABITBIInputs(patient_id="P1", as_of=AS_OF, abi_right=0.5, abi_left=1.1))
    assert result.clinical_status == ClinicalStatus.SUSPECTED


def test_additional_evidence_appended_to_action_text():
    result = calc.compute(
        ABITBIInputs(patient_id="P1", as_of=AS_OF, abi_right=0.5, abi_left=1.1, claudication_present=True)
    )
    assert "claudication present" in result.action
