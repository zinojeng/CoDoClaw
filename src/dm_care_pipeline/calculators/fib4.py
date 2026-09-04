"""
Tier A — FIB-4（Advanced liver fibrosis risk assessment）。

★ 鐵律1：公式與切點逐字取自 OpenClaw for Diabetes HIS.md §6.2：
    FIB-4 = Age × AST / (Platelet × sqrt(ALT))
    <1.3  → 較低 advanced fibrosis risk
    ≥1.3  → 考慮第二階段評估（FibroScan/VCTE、ELF、hepatology pathway）
不可自行調整切點。§6.2 明文「高齡及年輕患者需有 age-related interpretation
warning，而不是所有年齡機械性使用同一 cutoff」——本實作對年齡<35或>65附加
`warnings`，但**不**調整 1.3 這個切點本身（規格書未給出年齡分層切點）。
"""

from __future__ import annotations

import math
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

CALCULATOR_ID = "FIB4"
CALCULATOR_VERSION = "v1.0"
SPEC_REFERENCE = "OpenClaw for Diabetes HIS.md §6.2"
FIB4_THRESHOLD = 1.3
YOUNG_AGE_WARNING_THRESHOLD = 35
OLD_AGE_WARNING_THRESHOLD = 65


@dataclass(frozen=True)
class FIB4Inputs:
    patient_id: str
    as_of: date
    age_years: Optional[float] = None
    ast_u_l: Optional[float] = None
    alt_u_l: Optional[float] = None
    platelet_10e9_l: Optional[float] = None
    lab_date: Optional[date] = None


class FIB4Calculator:
    calculator_id: ClassVar[str] = CALCULATOR_ID
    calculator_version: ClassVar[str] = CALCULATOR_VERSION
    tier: ClassVar[CalculatorTier] = CalculatorTier.A
    required_inputs: ClassVar[tuple[str, ...]] = ("age_years", "ast_u_l", "alt_u_l", "platelet_10e9_l")

    def compute(self, inputs: FIB4Inputs) -> CalculatorResult:
        field_map = {
            "age_years": inputs.age_years,
            "ast_u_l": inputs.ast_u_l,
            "alt_u_l": inputs.alt_u_l,
            "platelet_10e9_l": inputs.platelet_10e9_l,
        }
        input_fields = tuple(
            CalculatorInputField(name=name, provided=value is not None, value=value, observed_date=inputs.lab_date)
            for name, value in field_map.items()
        )
        missing = tuple(name for name, value in field_map.items() if value is None)
        # ALT/Platelet = 0 會造成除以零/開根號分母為零，視為資料無效，
        # 一律 INSUFFICIENT_DATA（不得以例外/NaN 混入結果）。
        invalid_zero = (inputs.alt_u_l == 0) or (inputs.platelet_10e9_l == 0)

        if missing or invalid_zero:
            warnings = []
            if invalid_zero:
                warnings.append("ALT 或 Platelet 為 0，FIB-4 公式分母無效，無法計算")
            return CalculatorResult(
                calculator_id=self.calculator_id,
                calculator_version=self.calculator_version,
                tier=self.tier,
                patient_id=inputs.patient_id,
                computed_at=inputs.as_of,
                execution_status=CalculatorExecutionStatus.INSUFFICIENT_DATA,
                inputs=input_fields,
                missing_inputs=missing,
                spec_reference=SPEC_REFERENCE,
                warnings=tuple(warnings) or (f"缺少計算 FIB-4 所需輸入: {', '.join(missing)}",),
            )

        fib4_value = (inputs.age_years * inputs.ast_u_l) / (inputs.platelet_10e9_l * math.sqrt(inputs.alt_u_l))

        warnings: list[str] = []
        if inputs.age_years < YOUNG_AGE_WARNING_THRESHOLD or inputs.age_years > OLD_AGE_WARNING_THRESHOLD:
            warnings.append(
                f"病人年齡={inputs.age_years}，FIB-4 對過年輕或高齡患者的判讀需額外注意"
                "（OpenClaw HIS §6.2 明文要求 age-related interpretation warning，"
                f"本實作不因此調整 {FIB4_THRESHOLD} 切點本身）"
            )

        if fib4_value >= FIB4_THRESHOLD:
            clinical_status = ClinicalStatus.SUSPECTED
            interpretation = "Advanced fibrosis risk requires further assessment"
            action = "考慮第二階段評估：FibroScan / VCTE、ELF、hepatology pathway"
            action_grounded_in_spec = True
        else:
            clinical_status = None
            interpretation = "較低 advanced fibrosis risk"
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
            result_values={"fib4": fib4_value},
            result_summary=f"FIB-4 = {fib4_value:.2f}",
            interpretation=interpretation,
            action=action,
            clinical_status=clinical_status,
            spec_reference=SPEC_REFERENCE,
            is_placeholder_methodology=False,
            action_grounded_in_spec=action_grounded_in_spec,
            warnings=tuple(warnings),
        )
