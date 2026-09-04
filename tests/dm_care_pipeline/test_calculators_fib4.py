"""`calculators/fib4.py`（FIB-4）測試。"""

from __future__ import annotations

import math
from datetime import date

from dm_care_pipeline.calculators.base import CalculatorExecutionStatus
from dm_care_pipeline.calculators.fib4 import FIB4Calculator, FIB4Inputs
from dm_care_pipeline.clinical_data_object import ClinicalStatus

AS_OF = date(2024, 6, 1)
calc = FIB4Calculator()


def test_missing_inputs_is_insufficient_data():
    result = calc.compute(FIB4Inputs(patient_id="P1", as_of=AS_OF, age_years=50))
    assert result.execution_status == CalculatorExecutionStatus.INSUFFICIENT_DATA
    assert "ast_u_l" in result.missing_inputs


def test_zero_alt_is_insufficient_data_not_exception():
    result = calc.compute(
        FIB4Inputs(patient_id="P1", as_of=AS_OF, age_years=50, ast_u_l=40, alt_u_l=0, platelet_10e9_l=200)
    )
    assert result.execution_status == CalculatorExecutionStatus.INSUFFICIENT_DATA


def test_zero_platelet_is_insufficient_data_not_exception():
    result = calc.compute(
        FIB4Inputs(patient_id="P1", as_of=AS_OF, age_years=50, ast_u_l=40, alt_u_l=40, platelet_10e9_l=0)
    )
    assert result.execution_status == CalculatorExecutionStatus.INSUFFICIENT_DATA


def test_formula_matches_spec():
    result = calc.compute(
        FIB4Inputs(patient_id="P1", as_of=AS_OF, age_years=50, ast_u_l=40, alt_u_l=40, platelet_10e9_l=200)
    )
    expected = (50 * 40) / (200 * math.sqrt(40))
    assert result.result_values["fib4"] == expected
    assert result.execution_status == CalculatorExecutionStatus.COMPUTED


def test_above_threshold_is_suspected():
    result = calc.compute(
        FIB4Inputs(patient_id="P1", as_of=AS_OF, age_years=50, ast_u_l=100, alt_u_l=20, platelet_10e9_l=100)
    )
    assert result.result_values["fib4"] >= 1.3
    assert result.clinical_status == ClinicalStatus.SUSPECTED
    assert result.action_grounded_in_spec is True


def test_below_threshold_no_clinical_status():
    result = calc.compute(
        FIB4Inputs(patient_id="P1", as_of=AS_OF, age_years=50, ast_u_l=20, alt_u_l=40, platelet_10e9_l=300)
    )
    assert result.result_values["fib4"] < 1.3
    assert result.clinical_status is None


def test_young_age_adds_warning_but_does_not_change_threshold():
    result = calc.compute(
        FIB4Inputs(patient_id="P1", as_of=AS_OF, age_years=25, ast_u_l=100, alt_u_l=20, platelet_10e9_l=100)
    )
    assert any("年齡" in w for w in result.warnings)
    assert result.result_values["fib4"] >= 1.3
