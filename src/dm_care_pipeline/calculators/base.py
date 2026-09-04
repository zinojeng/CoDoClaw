"""
Calculator Library 共用契約（規格§13「未來增加 calculator 只增加一個
Calculator Module」的具體落地；規格§31「Calculator Result Object」）。

★★★ 鐵律2/鐵律3 的裁定落地 ★★★：本檔案把「計算工具有沒有算出來」的
`CalculatorExecutionStatus`（執行狀態，本檔案定義）與「算出來後對應哪個
臨床狀態」的 `ClinicalStatus`（規格§5 四值，`clinical_data_object.py`
定義）完全分離，兩者是正交的兩件事（詳見
docs/臨床決策支援管線設計_v2_OpenClaw.md 第2節命名統一總表）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import ClassVar, Optional, Protocol, runtime_checkable

from ..clinical_data_object import ClinicalStatus, ModelProvenance

__all__ = [
    "CalculatorTier",
    "CalculatorExecutionStatus",
    "CalculatorInputField",
    "CalculatorResult",
    "Calculator",
]


class CalculatorTier(str, Enum):
    """A＝規格書逐字公式/切點，可計算；B＝僅工具名稱+變數清單，無完整係數，
    僅可插拔介面（鐵律1/鐵律2）。"""

    A = "A"
    B = "B"


class CalculatorExecutionStatus(str, Enum):
    """★ 計算工具『有沒有算出來』的執行狀態，與 `ClinicalStatus`（算出來後
    對應哪個臨床狀態）完全分離。"""

    COMPUTED = "computed"
    INSUFFICIENT_DATA = "insufficient_data"
    NOT_APPLICABLE = "not_applicable"  # 例如 PREVENT 年齡不在30-79歲/已進入secondary prevention
    REQUIRES_EXTERNAL_VALIDATED_MODEL = "requires_external_validated_model"  # Tier B 固定回傳


@dataclass(frozen=True)
class CalculatorInputField:
    name: str
    provided: bool
    value: Optional[object] = None
    source: Optional[str] = None  # 例如 lab item_code 或 profile 欄位路徑
    observed_date: Optional[date] = None


@dataclass(frozen=True)
class CalculatorResult:
    """規格§31 Calculator Result Object，全管線唯一權威定義，本檔案（
    `calculators/base.py`）擁有；其餘站點一律 import 消費，不得自建同義
    型別（見架構文件v2 第2節命名統一總表）。"""

    calculator_id: str  # 例如 "KDIGO_GA"（比照§35 calculator/KDIGO_GA/v1）
    calculator_version: str  # 例如 "v1.0"
    tier: CalculatorTier
    patient_id: str
    computed_at: date
    execution_status: CalculatorExecutionStatus
    inputs: tuple[CalculatorInputField, ...] = ()
    missing_inputs: tuple[str, ...] = ()  # required_inputs 中缺漏者
    result_values: Optional[dict[str, object]] = None  # execution_status!=COMPUTED 時恆為 None（鐵律2的程式化落實）
    result_summary: Optional[str] = None  # 人類可讀，例如 "CKD G3aA2"
    interpretation: Optional[str] = None  # Tier B 恆 None（不得生成"High risk"等文字冒充已驗證判讀）
    action: Optional[str] = None
    clinical_status: Optional[ClinicalStatus] = None  # 規格§5四值，僅 execution_status==COMPUTED 時可能非 None
    guideline: Optional[str] = None
    spec_reference: str = ""
    is_placeholder_methodology: bool = False  # Tier A 恆 False；Tier B 恆 True
    action_grounded_in_spec: bool = False
    model_provenance: Optional[ModelProvenance] = None  # Tier B 必填
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # ★ 鐵律2 的程式化落實：execution_status!=COMPUTED 時 result_values
        # 恆為 None，避免任何「還沒算出來」的結果被 UI 誤讀為已驗證數字。
        if self.execution_status != CalculatorExecutionStatus.COMPUTED and self.result_values is not None:
            raise ValueError(
                f"CalculatorResult({self.calculator_id}): execution_status="
                f"{self.execution_status.value} 時 result_values 必須為 None，"
                f"不可帶有任何計算數值（鐵律2）"
            )
        if self.tier == CalculatorTier.B:
            # Tier B 永遠不得落入 COMPUTED（不可自行編造係數算出數值，鐵律2）。
            # NOT_APPLICABLE 例外允許（例如 PREVENT 命中 already_in_secondary_
            # prevention()/年齡範圍檢查——純路由說明，非風險計算，見架構文件
            # v2 §3.4），此時 interpretation 允許非 None；其餘一律
            # REQUIRES_EXTERNAL_VALIDATED_MODEL 且 interpretation 恆 None。
            allowed = (
                CalculatorExecutionStatus.REQUIRES_EXTERNAL_VALIDATED_MODEL,
                CalculatorExecutionStatus.NOT_APPLICABLE,
            )
            if self.execution_status not in allowed:
                raise ValueError(
                    f"CalculatorResult({self.calculator_id}): Tier B calculator 的 execution_status "
                    f"必須是 {[s.value for s in allowed]} 之一（鐵律2），收到 {self.execution_status.value}"
                )
            if self.execution_status == CalculatorExecutionStatus.REQUIRES_EXTERNAL_VALIDATED_MODEL:
                # ★ 修正（Codex 審閱發現）：先前只擋 interpretation，
                # 但 clinical_status/result_summary/is_placeholder_methodology
                # 皆未受限——一個寫壞或惡意的 Tier B 插件實作可以合法建構出
                # `execution_status=REQUIRES_EXTERNAL_VALIDATED_MODEL` 但同時
                # 帶 `clinical_status=HIGH_RISK`/`result_summary="風險極高"`/
                # `is_placeholder_methodology=False` 的 CalculatorResult，
                # 讓下游（`clinical_state.py` 的 COMPUTED 分支未涵蓋但
                # `guideline_recommendation.py`/`pre_visit_brief.py` 若直接
                # 讀 `clinical_status`/`result_summary` 顯示）誤把未驗證結果
                # 當真。本檔案是所有 CalculatorResult 建構的唯一守門處，
                # 於此一併鎖死，不留給下游各自防禦（鐵律2）。
                if self.interpretation is not None:
                    raise ValueError(
                        f"CalculatorResult({self.calculator_id}): execution_status="
                        "REQUIRES_EXTERNAL_VALIDATED_MODEL 時 interpretation 必須為 None，"
                        "不得生成判讀文字冒充已驗證結果（鐵律2）"
                    )
                if self.clinical_status is not None:
                    raise ValueError(
                        f"CalculatorResult({self.calculator_id}): execution_status="
                        "REQUIRES_EXTERNAL_VALIDATED_MODEL 時 clinical_status 必須為 None，"
                        "不得冒充已驗證的臨床狀態分級（鐵律2）"
                    )
                if self.result_summary is not None:
                    raise ValueError(
                        f"CalculatorResult({self.calculator_id}): execution_status="
                        "REQUIRES_EXTERNAL_VALIDATED_MODEL 時 result_summary 必須為 None，"
                        "不得帶任何看似已算出的摘要文字（鐵律2）"
                    )
                if not self.is_placeholder_methodology:
                    raise ValueError(
                        f"CalculatorResult({self.calculator_id}): execution_status="
                        "REQUIRES_EXTERNAL_VALIDATED_MODEL 時 is_placeholder_methodology 必須為 True"
                    )
            if self.model_provenance is None:
                raise ValueError(f"CalculatorResult({self.calculator_id}): Tier B calculator 必須填 model_provenance（鐵律2）")


@runtime_checkable
class Calculator(Protocol):
    calculator_id: ClassVar[str]
    calculator_version: ClassVar[str]
    tier: ClassVar[CalculatorTier]
    required_inputs: ClassVar[tuple[str, ...]]

    def compute(self, inputs) -> CalculatorResult: ...
