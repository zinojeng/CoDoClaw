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


def test_negative_alt_is_insufficient_data_not_exception():
    """回歸測試（Codex #19）：先前只擋 ALT==0，負的 ALT 會讓
    math.sqrt(負數) 直接拋 ValueError，讓整條管線崩潰而非回傳
    INSUFFICIENT_DATA。"""
    result = calc.compute(
        FIB4Inputs(patient_id="P1", as_of=AS_OF, age_years=50, ast_u_l=40, alt_u_l=-5, platelet_10e9_l=200)
    )
    assert result.execution_status == CalculatorExecutionStatus.INSUFFICIENT_DATA


def test_negative_platelet_is_insufficient_data_not_falsely_low_risk():
    """回歸測試（Codex #19）：負的 Platelet 先前會算出負的 FIB-4，因
    < 1.3 而被判為「較低風險」正常回傳——負值本身就是不可能的檢驗結果，
    不該被拿去算出一個看似合理的低風險數字。"""
    result = calc.compute(
        FIB4Inputs(patient_id="P1", as_of=AS_OF, age_years=50, ast_u_l=40, alt_u_l=40, platelet_10e9_l=-200)
    )
    assert result.execution_status == CalculatorExecutionStatus.INSUFFICIENT_DATA


def test_nan_ast_is_insufficient_data_not_falsely_low_risk():
    """回歸測試（Codex #19）：NaN 輸入先前會算出 NaN，因 NaN >= 1.3 為
    False 而被判為「較低風險」正常回傳。"""
    result = calc.compute(
        FIB4Inputs(patient_id="P1", as_of=AS_OF, age_years=50, ast_u_l=math.nan, alt_u_l=40, platelet_10e9_l=200)
    )
    assert result.execution_status == CalculatorExecutionStatus.INSUFFICIENT_DATA


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
