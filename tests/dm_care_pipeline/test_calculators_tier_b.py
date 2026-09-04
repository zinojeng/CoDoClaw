"""
Tier B calculators（WATCH-DM/PREVENT/Legacy ASCVD PCE/Karter/KFRE）測試。

鐵律2 的核心斷言：`execution_status` 恆為
`REQUIRES_EXTERNAL_VALIDATED_MODEL`（或路由用的 `NOT_APPLICABLE`）、
`result_values`/`interpretation` 恆為 None（NOT_APPLICABLE 例外）、
`model_provenance` 必填。
"""

from __future__ import annotations

from datetime import date

import pytest

from dm_care_pipeline.calculators.base import CalculatorExecutionStatus, CalculatorTier
from dm_care_pipeline.calculators.tier_b.karter_hypoglycemia import (
    KarterHypoglycemiaCalculator,
    KarterHypoglycemiaInputs,
)
from dm_care_pipeline.calculators.tier_b.kfre import Kfre4VarCalculator, Kfre4VarInputs
from dm_care_pipeline.calculators.tier_b.prevent_ascvd import (
    LegacyAscvdPceCalculator,
    LegacyAscvdPceInputs,
    PreventCalculator,
    PreventInputs,
    already_in_secondary_prevention,
)
from dm_care_pipeline.calculators.tier_b.watch_dm import WatchDmCalculator, WatchDmInputs
from dm_care_pipeline.calculators.tier_b import register_tier_b_calculators
from dm_care_pipeline.calculators.registry import CalculatorRegistry

AS_OF = date(2024, 6, 1)

ALL_TIER_B = [
    (WatchDmCalculator(), WatchDmInputs(patient_id="P1", as_of=AS_OF)),
    (KarterHypoglycemiaCalculator(), KarterHypoglycemiaInputs(patient_id="P1", as_of=AS_OF)),
    (Kfre4VarCalculator(), Kfre4VarInputs(patient_id="P1", as_of=AS_OF)),
]


@pytest.mark.parametrize("calc,inputs", ALL_TIER_B)
def test_tier_b_never_computes_a_number(calc, inputs):
    result = calc.compute(inputs)
    assert result.execution_status == CalculatorExecutionStatus.REQUIRES_EXTERNAL_VALIDATED_MODEL
    assert result.result_values is None
    assert result.interpretation is None
    assert result.is_placeholder_methodology is True
    assert result.model_provenance is not None
    assert result.model_provenance.taiwan_local_validation_status == "not_locally_validated"
    assert result.tier == CalculatorTier.B


@pytest.mark.parametrize("calc,inputs", ALL_TIER_B)
def test_tier_b_missing_inputs_reported(calc, inputs):
    result = calc.compute(inputs)
    assert set(result.missing_inputs) == set(calc.required_inputs)


def test_karter_full_inputs_still_requires_external_model():
    calc = KarterHypoglycemiaCalculator()
    result = calc.compute(
        KarterHypoglycemiaInputs(
            patient_id="P1",
            as_of=AS_OF,
            prior_hypo_related_ed_or_hosp=True,
            ed_visits_prior_12mo=2,
            insulin_use=True,
            sulfonylurea_use=False,
            ckd_stage_4_5_or_severe=False,
            age_years=70,
        )
    )
    assert result.execution_status == CalculatorExecutionStatus.REQUIRES_EXTERNAL_VALIDATED_MODEL
    assert result.result_values is None
    assert result.missing_inputs == ()


# ---------------------------------------------------------------------------
# already_in_secondary_prevention() 路由規則
# ---------------------------------------------------------------------------


def test_already_in_secondary_prevention_true_when_cvd_complication_present():
    assert already_in_secondary_prevention(frozenset({"CVD"})) is True


def test_already_in_secondary_prevention_true_when_revascularization_history():
    assert already_in_secondary_prevention(frozenset(), has_revascularization_history=True) is True


def test_already_in_secondary_prevention_false_otherwise():
    assert already_in_secondary_prevention(frozenset({"NEPHROPATHY"})) is False


def test_already_in_secondary_prevention_true_when_mi_acs_history():
    """回歸測試（Codex #21）：OpenClaw HIS §7 明文把「MI/ACS」列為 secondary
    prevention 觸發條件之一，獨立於 complications 集合是否含 CVD（I20-I24
    預設不算進 CVD 類別，見 ComplicationConfig.include_broader_ihd_codes）。"""
    assert already_in_secondary_prevention(frozenset(), has_mi_acs_history=True) is True


# ---------------------------------------------------------------------------
# PREVENT / Legacy ASCVD PCE 特有路由分支
# ---------------------------------------------------------------------------


def test_prevent_secondary_prevention_routes_to_not_applicable():
    calc = PreventCalculator()
    result = calc.compute(
        PreventInputs(patient_id="P1", as_of=AS_OF, age_years=60, complications=frozenset({"CVD"}))
    )
    assert result.execution_status == CalculatorExecutionStatus.NOT_APPLICABLE
    assert result.interpretation is not None
    assert result.action is None


def test_prevent_age_out_of_range_routes_to_not_applicable():
    calc = PreventCalculator()
    result = calc.compute(PreventInputs(patient_id="P1", as_of=AS_OF, age_years=85))
    assert result.execution_status == CalculatorExecutionStatus.NOT_APPLICABLE
    assert "PREVENT 適用範圍" in result.interpretation


def test_prevent_primary_prevention_in_range_requires_external_model():
    calc = PreventCalculator()
    result = calc.compute(PreventInputs(patient_id="P1", as_of=AS_OF, age_years=55))
    assert result.execution_status == CalculatorExecutionStatus.REQUIRES_EXTERNAL_VALIDATED_MODEL
    # complications/has_revascularization_history 不是模型變數，不應出現在 inputs
    assert all(
        f.name not in ("complications", "has_revascularization_history", "has_mi_acs_history") for f in result.inputs
    )


def test_prevent_missing_age_and_sex_reported_as_missing_inputs():
    """回歸測試（Codex #20）：age_years 先前被用於 compute() 的年齡範圍
    判斷，卻不在 required_inputs 裡，缺值時不會被回報成缺漏；sex/
    antihypertensive_treatment 是模型本身的變數，先前完全沒有欄位。"""
    calc = PreventCalculator()
    result = calc.compute(PreventInputs(patient_id="P1", as_of=AS_OF))
    assert "age_years" in result.missing_inputs
    assert "sex" in result.missing_inputs
    assert "antihypertensive_treatment" in result.missing_inputs


def test_prevent_uses_supplied_sex():
    calc = PreventCalculator()
    result = calc.compute(PreventInputs(patient_id="P1", as_of=AS_OF, age_years=55, sex="female"))
    sex_field = next(f for f in result.inputs if f.name == "sex")
    assert sex_field.provided is True
    assert sex_field.value == "female"


def test_prevent_mi_acs_history_routes_to_not_applicable():
    """回歸測試（Codex #21）：純 MI/ACS 病史（沒有 CVD 類別命中）應直接
    路由到 secondary prevention，不留在 primary prevention 分支。"""
    calc = PreventCalculator()
    result = calc.compute(
        PreventInputs(patient_id="P1", as_of=AS_OF, age_years=60, has_mi_acs_history=True)
    )
    assert result.execution_status == CalculatorExecutionStatus.NOT_APPLICABLE


def test_legacy_ascvd_secondary_prevention_routes_to_not_applicable():
    calc = LegacyAscvdPceCalculator()
    result = calc.compute(
        LegacyAscvdPceInputs(patient_id="P1", as_of=AS_OF, age_years=60, complications=frozenset({"PVD"}))
    )
    assert result.execution_status == CalculatorExecutionStatus.NOT_APPLICABLE


def test_legacy_ascvd_age_out_of_range_routes_to_not_applicable():
    calc = LegacyAscvdPceCalculator()
    result = calc.compute(LegacyAscvdPceInputs(patient_id="P1", as_of=AS_OF, age_years=30))
    assert result.execution_status == CalculatorExecutionStatus.NOT_APPLICABLE


def test_legacy_ascvd_in_range_requires_external_model():
    calc = LegacyAscvdPceCalculator()
    result = calc.compute(LegacyAscvdPceInputs(patient_id="P1", as_of=AS_OF, age_years=55))
    assert result.execution_status == CalculatorExecutionStatus.REQUIRES_EXTERNAL_VALIDATED_MODEL


def test_legacy_ascvd_missing_age_and_sex_reported_as_missing_inputs():
    """回歸測試（Codex #20），Legacy PCE 對照版本。"""
    calc = LegacyAscvdPceCalculator()
    result = calc.compute(LegacyAscvdPceInputs(patient_id="P1", as_of=AS_OF))
    assert "age_years" in result.missing_inputs
    assert "sex" in result.missing_inputs


def test_legacy_ascvd_mi_acs_history_routes_to_not_applicable():
    """回歸測試（Codex #21），Legacy PCE 對照版本。"""
    calc = LegacyAscvdPceCalculator()
    result = calc.compute(
        LegacyAscvdPceInputs(patient_id="P1", as_of=AS_OF, age_years=60, has_mi_acs_history=True)
    )
    assert result.execution_status == CalculatorExecutionStatus.NOT_APPLICABLE


# ---------------------------------------------------------------------------
# KFRE CKD 適用性判斷
# ---------------------------------------------------------------------------


def test_kfre_not_applicable_when_not_ckd():
    """回歸測試（Codex #22）：KFRE 定義即為 CKD 病人的腎衰竭風險預測
    （OpenClaw HIS §12），先前完全沒有適用性判斷，G1A1 這種明確非 CKD
    的病人也會收到 KFRE 的「待驗證模型」care gap。"""
    calc = Kfre4VarCalculator()
    result = calc.compute(Kfre4VarInputs(patient_id="P1", as_of=AS_OF, age_years=60, sex="female", egfr=95.0, uacr=10.0))
    assert result.execution_status == CalculatorExecutionStatus.NOT_APPLICABLE
    assert "G1A1" in result.interpretation


def test_kfre_requires_external_model_when_ckd():
    """正向對照：確實符合 CKD 定義時，仍走原本的 Tier B 流程（不冒充
    已驗證數值，只是不再對非 CKD 病人誤觸發）。"""
    calc = Kfre4VarCalculator()
    result = calc.compute(Kfre4VarInputs(patient_id="P1", as_of=AS_OF, age_years=60, sex="female", egfr=40.0, uacr=100.0))
    assert result.execution_status == CalculatorExecutionStatus.REQUIRES_EXTERNAL_VALIDATED_MODEL


def test_kfre_requires_external_model_when_ckd_status_undeterminable():
    """egfr/uacr 任一缺值時無法判斷是否為 CKD，不可假設「非 CKD」而跳過，
    也不可假設「是 CKD」而略過缺值檢查——一律走原本流程，缺值本身由
    required_inputs 標記。"""
    calc = Kfre4VarCalculator()
    result = calc.compute(Kfre4VarInputs(patient_id="P1", as_of=AS_OF, age_years=60, sex="female", egfr=95.0))
    assert result.execution_status == CalculatorExecutionStatus.REQUIRES_EXTERNAL_VALIDATED_MODEL
    assert "uacr" in result.missing_inputs


# ---------------------------------------------------------------------------
# register_tier_b_calculators()
# ---------------------------------------------------------------------------


def test_register_tier_b_calculators_registers_all_five():
    registry = CalculatorRegistry()
    register_tier_b_calculators(registry)
    ids = set(registry.list_calculator_ids())
    assert ids == {"WATCH_DM", "PREVENT", "ASCVD_PCE_2013", "KARTER_HYPO_ED_HOSP", "KFRE_4VAR"}
    assert all(r.tier == CalculatorTier.B for r in registry.list_calculators())
