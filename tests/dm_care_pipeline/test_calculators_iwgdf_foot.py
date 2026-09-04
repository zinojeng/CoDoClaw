"""`calculators/iwgdf_foot.py`（IWGDF Diabetic Foot Risk）測試。"""

from __future__ import annotations

from datetime import date, timedelta

from dm_care_pipeline.calculators.base import CalculatorExecutionStatus
from dm_care_pipeline.calculators.iwgdf_foot import (
    IWGDF_FOLLOWUP_INTERVAL_DAYS,
    IWGDFFootInputs,
    IWGDFFootRiskCalculator,
)
from dm_care_pipeline.clinical_data_object import ClinicalStatus

AS_OF = date(2024, 6, 1)
calc = IWGDFFootRiskCalculator()


def test_lops_pad_unknown_is_insufficient_data_not_defaulted_to_category0():
    result = calc.compute(IWGDFFootInputs(patient_id="P1", as_of=AS_OF))
    assert result.execution_status == CalculatorExecutionStatus.INSUFFICIENT_DATA
    assert set(result.missing_inputs) == {"lops_present", "pad_present"}


def test_category0_no_lops_no_pad():
    result = calc.compute(IWGDFFootInputs(patient_id="P1", as_of=AS_OF, lops_present=False, pad_present=False))
    assert result.result_values["category"] == 0
    assert result.clinical_status is None


def test_category1_lops_only():
    result = calc.compute(IWGDFFootInputs(patient_id="P1", as_of=AS_OF, lops_present=True, pad_present=False))
    assert result.result_values["category"] == 1
    assert result.clinical_status is None  # 只有 category 2/3 才是 HIGH_RISK（未逾期時）


def test_category2_lops_and_pad():
    result = calc.compute(IWGDFFootInputs(patient_id="P1", as_of=AS_OF, lops_present=True, pad_present=True))
    assert result.result_values["category"] == 2
    assert result.clinical_status == ClinicalStatus.HIGH_RISK


def test_category2_lops_and_deformity():
    result = calc.compute(
        IWGDFFootInputs(
            patient_id="P1", as_of=AS_OF, lops_present=True, pad_present=False, foot_deformity_present=True
        )
    )
    assert result.result_values["category"] == 2


def test_category3_lops_and_previous_ulcer():
    result = calc.compute(
        IWGDFFootInputs(patient_id="P1", as_of=AS_OF, lops_present=True, pad_present=False, previous_foot_ulcer=True)
    )
    assert result.result_values["category"] == 3
    assert result.clinical_status == ClinicalStatus.HIGH_RISK


def test_category3_pad_and_amputation_history():
    result = calc.compute(
        IWGDFFootInputs(
            patient_id="P1", as_of=AS_OF, lops_present=False, pad_present=True, previous_amputation=True
        )
    )
    assert result.result_values["category"] == 3


def test_overdue_uses_upper_bound_of_interval_and_sets_care_gap():
    _, upper = IWGDF_FOLLOWUP_INTERVAL_DAYS[1]
    last_eval = AS_OF - timedelta(days=upper + 1)
    result = calc.compute(
        IWGDFFootInputs(
            patient_id="P1",
            as_of=AS_OF,
            lops_present=True,
            pad_present=False,
            last_foot_evaluation_date=last_eval,
        )
    )
    assert result.result_values["overdue"] is True
    assert result.clinical_status == ClinicalStatus.CARE_GAP


def test_not_overdue_within_upper_bound():
    _, upper = IWGDF_FOLLOWUP_INTERVAL_DAYS[1]
    last_eval = AS_OF - timedelta(days=upper - 1)
    result = calc.compute(
        IWGDFFootInputs(
            patient_id="P1",
            as_of=AS_OF,
            lops_present=True,
            pad_present=False,
            last_foot_evaluation_date=last_eval,
        )
    )
    assert result.result_values["overdue"] is False


def test_missing_last_eval_date_leaves_overdue_unknown_not_false():
    result = calc.compute(IWGDFFootInputs(patient_id="P1", as_of=AS_OF, lops_present=False, pad_present=False))
    assert result.result_values["overdue"] is None
    assert any("overdue" in w for w in result.warnings)
