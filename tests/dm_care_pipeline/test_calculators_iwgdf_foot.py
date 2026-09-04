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


def test_unknown_high_risk_history_flags_possible_underestimation():
    """回歸測試（Codex #18）：LOPS 存在但 ulcer/amputation/kidney_failure
    病史皆未知時，先前只寫進 warnings 自由文字，missing_inputs 恆為空
    tuple——若那些未知欄位其實為真，真實 Category 應是 3，卻因未知值被
    當「無」算成 1。現在必須：① missing_inputs 結構化列出未知欄位；
    ② interpretation 明確標示「可能被低估」，不可讓 Category 1 看起來
    像已經排除高風險病史。"""
    result = calc.compute(
        IWGDFFootInputs(patient_id="P1", as_of=AS_OF, lops_present=True, pad_present=False)
    )
    assert result.result_values["category"] == 1  # 未知值保守處理為「無」，算出的分類本身不變
    assert set(result.missing_inputs) == {
        "foot_deformity_present",
        "previous_foot_ulcer",
        "previous_amputation",
        "kidney_failure_present",
    }
    assert "低估" in result.interpretation


def test_category0_no_missing_inputs_when_no_lops_or_pad():
    """正向對照：無 LOPS/PAD 時，即使高風險病史欄位未知，也不會被低估
    （Category 3 的前提本來就需要 LOPS 或 PAD），不應誤觸發低估警示。"""
    result = calc.compute(IWGDFFootInputs(patient_id="P1", as_of=AS_OF, lops_present=False, pad_present=False))
    assert result.result_values["category"] == 0
    assert "低估" not in result.interpretation


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
