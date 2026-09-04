"""
Tier B 共用骨架。

★★★ 鐵律2 ★★★：WATCH-DM/PREVENT/Legacy ASCVD PCE/Karter Hypoglycemia/
KFRE 這幾個工具，規格書只給出「這是哪個已發表驗證工具、需要哪些變數、
大致風險分級」，沒有給出完整回歸係數/計分表。本檔案提供的
`TierBCalculatorBase` 確保所有子類別：
    - `execution_status` 恆為 `REQUIRES_EXTERNAL_VALIDATED_MODEL`
      （`PreventCalculator`/`LegacyAscvdPceCalculator` 的純路由分支
      例外，回傳 `NOT_APPLICABLE`，見 `prevent_ascvd.py`）
    - `result_values` 恆為 None、`interpretation` 恆為 None
    - `model_provenance` 必填，`warnings` 固定引用 §37 Local Validation
      原文精神（`LOCAL_VALIDATION_WARNING`）
不可自行編造係數硬算出一個數字冒充已驗證結果。介面設計讓未來接上真正
已驗證的計算服務時，不需要改動呼叫端程式碼——只需要替換
`CalculatorRegistry` 中對應 `calculator_id` 的實作即可。
"""

from __future__ import annotations

import dataclasses
from typing import ClassVar

from ...clinical_data_object import LOCAL_VALIDATION_WARNING, ModelProvenance
from ..base import (
    CalculatorExecutionStatus,
    CalculatorInputField,
    CalculatorResult,
    CalculatorTier,
)

TIER_B_ACTION_TEXT = (
    "本工具需已通過台灣本地驗證/校正之計算服務方可產生風險數值，"
    "目前僅顯示所需變數是否齊備；請勿以工程佔位公式推算實際風險"
    "（依規格書§37 Local Validation 精神）。"
)


class TierBCalculatorBase:
    """子類別只需提供 `calculator_id`/`calculator_version`/
    `required_inputs`/`spec_reference`，並覆寫 `_model_provenance()`。
    `compute()` 對非 patient_id/as_of 的 dataclass 欄位一律視為輸入變數，
    自動組裝 `CalculatorInputField`/`missing_inputs`。"""

    calculator_id: ClassVar[str]
    calculator_version: ClassVar[str]
    tier: ClassVar[CalculatorTier] = CalculatorTier.B
    required_inputs: ClassVar[tuple[str, ...]] = ()
    spec_reference: ClassVar[str] = ""
    guideline: ClassVar[str | None] = None

    def _model_provenance(self) -> ModelProvenance:
        raise NotImplementedError

    def _extract_field_values(self, inputs) -> dict[str, object]:
        return {
            f.name: getattr(inputs, f.name)
            for f in dataclasses.fields(inputs)
            if f.name not in ("patient_id", "as_of")
        }

    def compute(self, inputs) -> CalculatorResult:
        field_values = self._extract_field_values(inputs)
        input_fields = tuple(
            CalculatorInputField(name=name, provided=value is not None, value=value)
            for name, value in field_values.items()
        )
        missing = tuple(name for name in self.required_inputs if field_values.get(name) is None)

        return CalculatorResult(
            calculator_id=self.calculator_id,
            calculator_version=self.calculator_version,
            tier=CalculatorTier.B,
            patient_id=getattr(inputs, "patient_id"),
            computed_at=getattr(inputs, "as_of"),
            execution_status=CalculatorExecutionStatus.REQUIRES_EXTERNAL_VALIDATED_MODEL,
            inputs=input_fields,
            missing_inputs=missing,
            result_values=None,
            result_summary=None,
            interpretation=None,
            action=TIER_B_ACTION_TEXT,
            clinical_status=None,
            guideline=self.guideline,
            spec_reference=self.spec_reference,
            is_placeholder_methodology=True,
            action_grounded_in_spec=False,
            model_provenance=self._model_provenance(),
            warnings=(LOCAL_VALIDATION_WARNING,),
        )
