"""
`calculators/base.py`（CalculatorResult 不變量）與 `calculators/registry.py`
（CalculatorRegistry 版本控制）測試。
"""

from __future__ import annotations

from datetime import date

import pytest

from dm_care_pipeline.calculators.base import (
    CalculatorExecutionStatus,
    CalculatorResult,
    CalculatorTier,
)
from dm_care_pipeline.calculators.ckd_ga import CKDGACalculator
from dm_care_pipeline.calculators.registry import (
    CalculatorNotFoundError,
    CalculatorRegistry,
)
from dm_care_pipeline.calculators.tier_b.watch_dm import WatchDmCalculator
from dm_care_pipeline.clinical_data_object import ClinicalStatus, ModelProvenance

AS_OF = date(2024, 6, 1)


def _base_kwargs(**overrides):
    defaults = dict(
        calculator_id="X",
        calculator_version="v1.0",
        tier=CalculatorTier.A,
        patient_id="P1",
        computed_at=AS_OF,
        execution_status=CalculatorExecutionStatus.COMPUTED,
    )
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# CalculatorResult 不變量（鐵律2 程式化落實）
# ---------------------------------------------------------------------------


def test_non_computed_result_with_result_values_raises():
    with pytest.raises(ValueError):
        CalculatorResult(**_base_kwargs(execution_status=CalculatorExecutionStatus.INSUFFICIENT_DATA, result_values={"x": 1}))


def test_tier_b_computed_status_raises():
    with pytest.raises(ValueError):
        CalculatorResult(
            **_base_kwargs(
                tier=CalculatorTier.B,
                execution_status=CalculatorExecutionStatus.COMPUTED,
                model_provenance=ModelProvenance(model_name="X", original_population="Y"),
            )
        )


def test_tier_b_requires_external_model_with_interpretation_raises():
    with pytest.raises(ValueError):
        CalculatorResult(
            **_base_kwargs(
                tier=CalculatorTier.B,
                execution_status=CalculatorExecutionStatus.REQUIRES_EXTERNAL_VALIDATED_MODEL,
                interpretation="High risk",
                model_provenance=ModelProvenance(model_name="X", original_population="Y"),
            )
        )


def test_tier_b_requires_external_model_with_clinical_status_raises():
    """回歸測試（Codex 審閱發現的真實 gap）：先前只擋 interpretation，
    未擋 clinical_status——一個寫壞的 Tier B 插件可以合法建構出
    「REQUIRES_EXTERNAL_VALIDATED_MODEL 但 clinical_status=HIGH_RISK」的
    結果，讓下游誤把未驗證結果當真。"""
    with pytest.raises(ValueError, match="clinical_status"):
        CalculatorResult(
            **_base_kwargs(
                tier=CalculatorTier.B,
                execution_status=CalculatorExecutionStatus.REQUIRES_EXTERNAL_VALIDATED_MODEL,
                clinical_status=ClinicalStatus.HIGH_RISK,
                model_provenance=ModelProvenance(model_name="X", original_population="Y"),
            )
        )


def test_tier_b_requires_external_model_with_result_summary_raises():
    with pytest.raises(ValueError, match="result_summary"):
        CalculatorResult(
            **_base_kwargs(
                tier=CalculatorTier.B,
                execution_status=CalculatorExecutionStatus.REQUIRES_EXTERNAL_VALIDATED_MODEL,
                result_summary="風險極高",
                model_provenance=ModelProvenance(model_name="X", original_population="Y"),
            )
        )


def test_tier_b_requires_external_model_claiming_non_placeholder_methodology_raises():
    with pytest.raises(ValueError, match="is_placeholder_methodology"):
        CalculatorResult(
            **_base_kwargs(
                tier=CalculatorTier.B,
                execution_status=CalculatorExecutionStatus.REQUIRES_EXTERNAL_VALIDATED_MODEL,
                is_placeholder_methodology=False,
                model_provenance=ModelProvenance(model_name="X", original_population="Y"),
            )
        )


def test_tier_b_without_model_provenance_raises():
    with pytest.raises(ValueError):
        CalculatorResult(
            **_base_kwargs(
                tier=CalculatorTier.B,
                execution_status=CalculatorExecutionStatus.REQUIRES_EXTERNAL_VALIDATED_MODEL,
            )
        )


def test_tier_b_not_applicable_allows_interpretation():
    result = CalculatorResult(
        **_base_kwargs(
            tier=CalculatorTier.B,
            execution_status=CalculatorExecutionStatus.NOT_APPLICABLE,
            interpretation="already in secondary prevention",
            model_provenance=ModelProvenance(model_name="X", original_population="Y"),
        )
    )
    assert result.interpretation == "already in secondary prevention"


def test_tier_a_computed_result_ok():
    result = CalculatorResult(**_base_kwargs(result_values={"x": 1}))
    assert result.result_values == {"x": 1}


# ---------------------------------------------------------------------------
# CalculatorRegistry
# ---------------------------------------------------------------------------


def test_registry_register_and_get_latest():
    registry = CalculatorRegistry()
    registry.register(CKDGACalculator())
    calc = registry.get("KDIGO_GA")
    assert isinstance(calc, CKDGACalculator)


def test_registry_qualified_key_matches_spec_format():
    registry = CalculatorRegistry()
    registry.register(CKDGACalculator())
    reg = registry.get_registration("KDIGO_GA")
    assert reg.qualified_key == "calculator/KDIGO_GA/v1.0"


def test_registry_get_by_qualified_key():
    registry = CalculatorRegistry()
    registry.register(CKDGACalculator())
    calc = registry.get_by_qualified_key("calculator/KDIGO_GA/v1.0")
    assert isinstance(calc, CKDGACalculator)


def test_registry_get_by_qualified_key_bad_format_raises():
    registry = CalculatorRegistry()
    with pytest.raises(CalculatorNotFoundError):
        registry.get_by_qualified_key("not-a-qualified-key")


def test_registry_unknown_calculator_raises():
    registry = CalculatorRegistry()
    with pytest.raises(CalculatorNotFoundError):
        registry.get("NO_SUCH_CALCULATOR")


def test_registry_list_calculators_filters_by_tier():
    registry = CalculatorRegistry()
    registry.register(CKDGACalculator())
    registry.register(WatchDmCalculator())
    tier_a_only = registry.list_calculators(tier=CalculatorTier.A)
    assert {r.calculator_id for r in tier_a_only} == {"KDIGO_GA"}
    assert {r.calculator_id for r in registry.list_calculators()} == {"KDIGO_GA", "WATCH_DM"}


def test_registry_multiple_versions_coexist_and_explicit_version_wins():
    registry = CalculatorRegistry()

    class _V1(CKDGACalculator):
        calculator_version = "v1.0"

    class _V2(CKDGACalculator):
        calculator_version = "v2.0"

    registry.register(_V1())
    registry.register(_V2())  # is_latest=True 預設，v2.0 成為 latest

    assert registry.get("KDIGO_GA").calculator_version == "v2.0"
    assert registry.get("KDIGO_GA", version="v1.0").calculator_version == "v1.0"


def test_registry_compute_delegates_to_calculator():
    from dm_care_pipeline.calculators.ckd_ga import CKDGAInputs

    registry = CalculatorRegistry()
    registry.register(CKDGACalculator())
    result = registry.compute("KDIGO_GA", CKDGAInputs(patient_id="P1", as_of=AS_OF, egfr=95.0, uacr=10.0))
    assert result.execution_status == CalculatorExecutionStatus.COMPUTED
    assert result.result_summary == "CKD G1A1"


def test_registry_compute_rejects_result_with_mismatched_calculator_id():
    """回歸測試（Codex 審閱發現）：registry 是所有 calculator 呼叫的唯一
    入口，須交叉驗證回傳的 CalculatorResult 真的屬於被呼叫的 calculator_id/
    tier/patient_id，防止寫壞或惡意的插件實作偽造身份讓下游誤信。"""
    from dm_care_pipeline.calculators.base import Calculator, CalculatorTier

    class _IdentitySpoofingCalculator:
        calculator_id = "SPOOFER"
        calculator_version = "v1.0"
        tier = CalculatorTier.A
        required_inputs = ()

        def compute(self, inputs) -> CalculatorResult:
            return CalculatorResult(
                calculator_id="KDIGO_GA",  # 偽造成別的 calculator_id
                calculator_version="v1.0",
                tier=CalculatorTier.A,
                patient_id=inputs.patient_id,
                computed_at=AS_OF,
                execution_status=CalculatorExecutionStatus.COMPUTED,
                result_values={"fake": "data"},
            )

    registry = CalculatorRegistry()
    registry.register(_IdentitySpoofingCalculator())

    from dataclasses import dataclass as _dataclass

    @_dataclass
    class _Inputs:
        patient_id: str

    with pytest.raises(ValueError, match="不一致"):
        registry.compute("SPOOFER", _Inputs(patient_id="P1"))


def test_registry_compute_rejects_result_with_mismatched_patient_id():
    from dataclasses import dataclass as _dataclass

    class _WrongPatientCalculator:
        calculator_id = "WRONG_PATIENT"
        calculator_version = "v1.0"
        tier = CalculatorTier.A
        required_inputs = ()

        def compute(self, inputs) -> CalculatorResult:
            return CalculatorResult(
                calculator_id="WRONG_PATIENT",
                calculator_version="v1.0",
                tier=CalculatorTier.A,
                patient_id="SOME_OTHER_PATIENT",
                computed_at=AS_OF,
                execution_status=CalculatorExecutionStatus.COMPUTED,
                result_values={"x": 1},
            )

    @_dataclass
    class _Inputs:
        patient_id: str

    registry = CalculatorRegistry()
    registry.register(_WrongPatientCalculator())
    with pytest.raises(ValueError, match="patient_id"):
        registry.compute("WRONG_PATIENT", _Inputs(patient_id="P1"))


def test_registry_compute_rejects_result_with_mismatched_version():
    """回歸測試（Codex 審閱發現）：規格§35/§36 版本控制/Audit Trail 的
    核心需求是「用哪一版公式算出的結果需可完整追溯」，registry 必須確保
    要求 version="v1.0" 就是真的拿到 v1.0 算出的結果。"""
    from dataclasses import dataclass as _dataclass

    class _WrongVersionCalculator:
        calculator_id = "WRONG_VERSION"
        calculator_version = "v1.0"
        tier = CalculatorTier.A
        required_inputs = ()

        def compute(self, inputs) -> CalculatorResult:
            return CalculatorResult(
                calculator_id="WRONG_VERSION",
                calculator_version="v2.0",  # 註冊為 v1.0，卻自稱 v2.0
                tier=CalculatorTier.A,
                patient_id=inputs.patient_id,
                computed_at=AS_OF,
                execution_status=CalculatorExecutionStatus.COMPUTED,
                result_values={"x": 1},
            )

    @_dataclass
    class _Inputs:
        patient_id: str

    registry = CalculatorRegistry()
    registry.register(_WrongVersionCalculator())
    with pytest.raises(ValueError, match="version"):
        registry.compute("WRONG_VERSION", _Inputs(patient_id="P1"))
