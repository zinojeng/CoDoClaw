"""
Tier A — ABI / TBI PAD Screening。

★ 鐵律1：切點逐字取自 OpenClaw for Diabetes HIS.md §6.5：
    ABI <= 0.90       → abnormal
    ABI >  1.40       → noncompressible（改看 TBI）
    TBI <= 0.70       → abnormal
並整合 claudication/pedal pulse/ulcer 等作為附加證據（不改變門檻）。

左右肢分開評估（規格書以「PAD screening/evaluation」整體描述，未逐字區分
單側/雙側判讀規則；本實作以「任一肢異常即整體 SUSPECTED」為工程規則化
詮釋，理由：PAD 為血管病變，臨床上單側異常已具篩檢意義，需臨床覆核此
聚合規則）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import ClassVar, Optional

from ..clinical_data_object import ClinicalStatus
from .base import (
    CalculatorExecutionStatus,
    CalculatorInputField,
    CalculatorResult,
    CalculatorTier,
)

CALCULATOR_ID = "ABI_TBI_PAD_SCREEN"
CALCULATOR_VERSION = "v1.0"
SPEC_REFERENCE = "OpenClaw for Diabetes HIS.md §6.5"
ABI_ABNORMAL_THRESHOLD = 0.90  # ABI <= 此值 → abnormal
ABI_NONCOMPRESSIBLE_THRESHOLD = 1.40  # ABI > 此值 → noncompressible，改看 TBI
TBI_ABNORMAL_THRESHOLD = 0.70  # TBI <= 此值 → abnormal


@dataclass(frozen=True)
class ABITBIInputs:
    patient_id: str
    as_of: date
    abi_right: Optional[float] = None
    abi_left: Optional[float] = None
    tbi_right: Optional[float] = None
    tbi_left: Optional[float] = None
    measurement_date: Optional[date] = None
    claudication_present: Optional[bool] = None
    pedal_pulse_abnormal: Optional[bool] = None
    ulcer_present: Optional[bool] = None


@dataclass(frozen=True)
class _SideEvaluation:
    side: str
    status: str  # "normal" | "abnormal" | "insufficient_data"
    detail: str


def _evaluate_side(side: str, abi: Optional[float], tbi: Optional[float]) -> _SideEvaluation:
    if abi is None and tbi is None:
        return _SideEvaluation(side, "insufficient_data", f"{side}: ABI/TBI 皆缺")

    if abi is not None:
        if abi <= ABI_ABNORMAL_THRESHOLD:
            return _SideEvaluation(side, "abnormal", f"{side}: ABI={abi}（<=0.90，abnormal）")
        if abi > ABI_NONCOMPRESSIBLE_THRESHOLD:
            # noncompressible，需改看 TBI
            if tbi is None:
                return _SideEvaluation(side, "insufficient_data", f"{side}: ABI={abi}（noncompressible），缺 TBI 無法判讀")
            if tbi <= TBI_ABNORMAL_THRESHOLD:
                return _SideEvaluation(side, "abnormal", f"{side}: ABI={abi}（noncompressible），TBI={tbi}（<=0.70，abnormal）")
            return _SideEvaluation(side, "normal", f"{side}: ABI={abi}（noncompressible），TBI={tbi}（正常）")
        return _SideEvaluation(side, "normal", f"{side}: ABI={abi}（正常範圍）")

    # 只有 TBI，沒有 ABI
    if tbi <= TBI_ABNORMAL_THRESHOLD:
        return _SideEvaluation(side, "abnormal", f"{side}: TBI={tbi}（<=0.70，abnormal，ABI缺值）")
    return _SideEvaluation(side, "normal", f"{side}: TBI={tbi}（正常，ABI缺值）")


class ABITBICalculator:
    calculator_id: ClassVar[str] = CALCULATOR_ID
    calculator_version: ClassVar[str] = CALCULATOR_VERSION
    tier: ClassVar[CalculatorTier] = CalculatorTier.A
    required_inputs: ClassVar[tuple[str, ...]] = ("abi_right", "abi_left")

    def compute(self, inputs: ABITBIInputs) -> CalculatorResult:
        input_fields = (
            CalculatorInputField(name="abi_right", provided=inputs.abi_right is not None, value=inputs.abi_right, observed_date=inputs.measurement_date),
            CalculatorInputField(name="abi_left", provided=inputs.abi_left is not None, value=inputs.abi_left, observed_date=inputs.measurement_date),
            CalculatorInputField(name="tbi_right", provided=inputs.tbi_right is not None, value=inputs.tbi_right, observed_date=inputs.measurement_date),
            CalculatorInputField(name="tbi_left", provided=inputs.tbi_left is not None, value=inputs.tbi_left, observed_date=inputs.measurement_date),
        )

        right = _evaluate_side("right", inputs.abi_right, inputs.tbi_right)
        left = _evaluate_side("left", inputs.abi_left, inputs.tbi_left)

        if right.status == "insufficient_data" and left.status == "insufficient_data":
            return CalculatorResult(
                calculator_id=self.calculator_id,
                calculator_version=self.calculator_version,
                tier=self.tier,
                patient_id=inputs.patient_id,
                computed_at=inputs.as_of,
                execution_status=CalculatorExecutionStatus.INSUFFICIENT_DATA,
                inputs=input_fields,
                missing_inputs=("abi_right", "abi_left", "tbi_right", "tbi_left"),
                spec_reference=SPEC_REFERENCE,
                warnings=(right.detail, left.detail),
            )

        additional_evidence = []
        if inputs.claudication_present:
            additional_evidence.append("claudication present")
        if inputs.pedal_pulse_abnormal:
            additional_evidence.append("pedal pulse abnormal")
        if inputs.ulcer_present:
            additional_evidence.append("ulcer present")

        abnormal = right.status == "abnormal" or left.status == "abnormal"
        warnings = []
        if right.status == "insufficient_data":
            warnings.append(right.detail)
        if left.status == "insufficient_data":
            warnings.append(left.detail)

        result_summary = f"{right.detail}; {left.detail}"
        if abnormal:
            clinical_status = ClinicalStatus.SUSPECTED
            interpretation = "PAD screening abnormal（ABI/TBI異常）"
            action = "整合 claudication/pedal pulse/ulcer/vascular imaging，評估 PAD 後續處置"
            if additional_evidence:
                action += f"；已知附加證據: {', '.join(additional_evidence)}"
            action_grounded_in_spec = True
        else:
            clinical_status = None
            interpretation = "PAD screening 未達異常切點"
            action = None
            action_grounded_in_spec = False

        return CalculatorResult(
            calculator_id=self.calculator_id,
            calculator_version=self.calculator_version,
            tier=self.tier,
            patient_id=inputs.patient_id,
            computed_at=inputs.as_of,
            execution_status=CalculatorExecutionStatus.COMPUTED,
            inputs=input_fields,
            missing_inputs=(),
            result_values={
                "abi_right": inputs.abi_right,
                "abi_left": inputs.abi_left,
                "tbi_right": inputs.tbi_right,
                "tbi_left": inputs.tbi_left,
                "right_status": right.status,
                "left_status": left.status,
            },
            result_summary=result_summary,
            interpretation=interpretation,
            action=action,
            clinical_status=clinical_status,
            spec_reference=SPEC_REFERENCE,
            is_placeholder_methodology=False,
            action_grounded_in_spec=action_grounded_in_spec,
            warnings=tuple(warnings),
        )
