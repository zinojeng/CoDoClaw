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
    assert all(f.name not in ("complications", "has_revascularization_history") for f in result.inputs)


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


# ---------------------------------------------------------------------------
# register_tier_b_calculators()
# ---------------------------------------------------------------------------


def test_register_tier_b_calculators_registers_all_five():
    registry = CalculatorRegistry()
    register_tier_b_calculators(registry)
    ids = set(registry.list_calculator_ids())
    assert ids == {"WATCH_DM", "PREVENT", "ASCVD_PCE_2013", "KARTER_HYPO_ED_HOSP", "KFRE_4VAR"}
    assert all(r.tier == CalculatorTier.B for r in registry.list_calculators())
