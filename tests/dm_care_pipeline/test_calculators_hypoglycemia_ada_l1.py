"""`calculators/hypoglycemia_ada_l1.py`（ADA Level 1 Hypoglycemia Risk）測試。"""

from __future__ import annotations

from datetime import date

from dm_care_pipeline.calculators.base import CalculatorExecutionStatus
from dm_care_pipeline.calculators.hypoglycemia_ada_l1 import (
    ADAHypoglycemiaLevel1Calculator,
    HypoglycemiaRiskFactorInputs,
)
from dm_care_pipeline.clinical_data_object import ClinicalStatus

AS_OF = date(2024, 6, 1)
calc = ADAHypoglycemiaLevel1Calculator()


def test_medication_status_all_unknown_is_insufficient_data():
    result = calc.compute(HypoglycemiaRiskFactorInputs(patient_id="P1", as_of=AS_OF))
    assert result.execution_status == CalculatorExecutionStatus.INSUFFICIENT_DATA


def test_no_relevant_medication_is_low_risk():
    result = calc.compute(
        HypoglycemiaRiskFactorInputs(patient_id="P1", as_of=AS_OF, on_insulin=False, on_sulfonylurea=False, on_meglitinide=False)
    )
    assert result.execution_status == CalculatorExecutionStatus.COMPUTED
    assert result.result_values["risk_level"] == "LOW"
    assert result.clinical_status is None


def test_on_medication_but_risk_factors_not_assessed_is_insufficient_data():
    result = calc.compute(HypoglycemiaRiskFactorInputs(patient_id="P1", as_of=AS_OF, on_insulin=True))
    assert result.execution_status == CalculatorExecutionStatus.INSUFFICIENT_DATA
    assert "risk_factors_assessed" in result.missing_inputs


def test_major_factor_present_is_high_risk():
    result = calc.compute(
        HypoglycemiaRiskFactorInputs(
            patient_id="P1",
            as_of=AS_OF,
            on_insulin=True,
            risk_factors_assessed=True,
            major_factors=frozenset({"kidney_failure"}),
        )
    )
    assert result.result_values["risk_level"] == "HIGH"
    assert result.clinical_status == ClinicalStatus.HIGH_RISK


def test_only_minor_factor_is_moderate_with_no_clinical_status():
    result = calc.compute(
        HypoglycemiaRiskFactorInputs(
            patient_id="P1",
            as_of=AS_OF,
            on_insulin=True,
            risk_factors_assessed=True,
            minor_factors=frozenset({"age_75_or_older"}),
        )
    )
    assert result.result_values["risk_level"] == "MODERATE"
    assert result.clinical_status is None  # §5 四態無中度層級，刻意不臆造


def test_assessed_with_no_factors_is_low():
    result = calc.compute(
        HypoglycemiaRiskFactorInputs(patient_id="P1", as_of=AS_OF, on_insulin=True, risk_factors_assessed=True)
    )
    assert result.result_values["risk_level"] == "LOW"
    assert result.clinical_status is None


def test_unknown_factor_code_still_counted_but_warns():
    result = calc.compute(
        HypoglycemiaRiskFactorInputs(
            patient_id="P1",
            as_of=AS_OF,
            on_insulin=True,
            risk_factors_assessed=True,
            major_factors=frozenset({"not_a_real_code"}),
        )
    )
    assert result.result_values["risk_level"] == "HIGH"
    assert any("未登記" in w for w in result.warnings)
