"""
Tier A — BNP / NT-proBNP Heart Failure Screening。

★ 鐵律1：切點逐字取自 OpenClaw for Diabetes HIS.md §6.4：
    BNP >= 50 pg/mL  或  NT-proBNP >= 125 pg/mL  → abnormal screening biomarker
若異常 → 建議 Echocardiography，評估 stage B HF / structural heart disease。
CKD/AF/age/pulmonary disease/anemia/obesity 等僅作為 interpretation
modifier（附加說明文字），**不改變門檻數值**（§6.4 原文：「NT-proBNP
abnormal 本身不是 HF diagnosis」）。
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

CALCULATOR_ID = "BNP_NTPROBNP_HF_SCREEN"
CALCULATOR_VERSION = "v1.0"
SPEC_REFERENCE = "OpenClaw for Diabetes HIS.md §6.4"
BNP_THRESHOLD_PG_ML = 50.0
NT_PROBNP_THRESHOLD_PG_ML = 125.0


@dataclass(frozen=True)
class NatriureticPeptideInputs:
    patient_id: str
    as_of: date
    bnp_pg_ml: Optional[float] = None
    nt_probnp_pg_ml: Optional[float] = None
    result_date: Optional[date] = None
    has_ckd: Optional[bool] = None
    has_atrial_fibrillation: Optional[bool] = None
    age_years: Optional[float] = None
    has_pulmonary_disease: Optional[bool] = None
    has_anemia: Optional[bool] = None
    has_obesity: Optional[bool] = None


class NatriureticPeptideHFScreenCalculator:
    calculator_id: ClassVar[str] = CALCULATOR_ID
    calculator_version: ClassVar[str] = CALCULATOR_VERSION
    tier: ClassVar[CalculatorTier] = CalculatorTier.A
    required_inputs: ClassVar[tuple[str, ...]] = ("bnp_pg_ml", "nt_probnp_pg_ml")

    def compute(self, inputs: NatriureticPeptideInputs) -> CalculatorResult:
        input_fields = (
            CalculatorInputField(
                name="bnp_pg_ml", provided=inputs.bnp_pg_ml is not None, value=inputs.bnp_pg_ml, observed_date=inputs.result_date
            ),
            CalculatorInputField(
                name="nt_probnp_pg_ml",
                provided=inputs.nt_probnp_pg_ml is not None,
                value=inputs.nt_probnp_pg_ml,
                observed_date=inputs.result_date,
            ),
        )

        if inputs.bnp_pg_ml is None and inputs.nt_probnp_pg_ml is None:
            return CalculatorResult(
                calculator_id=self.calculator_id,
                calculator_version=self.calculator_version,
                tier=self.tier,
                patient_id=inputs.patient_id,
                computed_at=inputs.as_of,
                execution_status=CalculatorExecutionStatus.INSUFFICIENT_DATA,
                inputs=input_fields,
                missing_inputs=("bnp_pg_ml", "nt_probnp_pg_ml"),
                spec_reference=SPEC_REFERENCE,
                warnings=("BNP 與 NT-proBNP 皆缺，無法進行 HF screening",),
            )

        bnp_abnormal = inputs.bnp_pg_ml is not None and inputs.bnp_pg_ml >= BNP_THRESHOLD_PG_ML
        nt_probnp_abnormal = inputs.nt_probnp_pg_ml is not None and inputs.nt_probnp_pg_ml >= NT_PROBNP_THRESHOLD_PG_ML
        abnormal = bnp_abnormal or nt_probnp_abnormal

        modifiers = []
        if inputs.has_ckd:
            modifiers.append("CKD")
        if inputs.has_atrial_fibrillation:
            modifiers.append("atrial fibrillation")
        if inputs.age_years is not None:
            modifiers.append(f"age={inputs.age_years}")
        if inputs.has_pulmonary_disease:
            modifiers.append("pulmonary disease")
        if inputs.has_anemia:
            modifiers.append("anemia")
        if inputs.has_obesity:
            modifiers.append("obesity")
        warnings = tuple()
        if modifiers:
            warnings = (
                f"interpretation modifier（不改變門檻，僅供判讀參考，OpenClaw HIS §6.4）: "
                f"{', '.join(modifiers)}",
            )

        result_summary_parts = []
        if inputs.bnp_pg_ml is not None:
            result_summary_parts.append(f"BNP={inputs.bnp_pg_ml}pg/mL")
        if inputs.nt_probnp_pg_ml is not None:
            result_summary_parts.append(f"NT-proBNP={inputs.nt_probnp_pg_ml}pg/mL")

        if abnormal:
            clinical_status = ClinicalStatus.SUSPECTED
            interpretation = "Abnormal natriuretic peptide screening biomarker（本身不是HF diagnosis）"
            action = "安排 Echocardiography，評估 stage B HF / structural heart disease"
            action_grounded_in_spec = True
        else:
            clinical_status = None
            interpretation = "Natriuretic peptide 未達 abnormal screening 切點"
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
            result_values={"bnp_pg_ml": inputs.bnp_pg_ml, "nt_probnp_pg_ml": inputs.nt_probnp_pg_ml},
            result_summary=", ".join(result_summary_parts) if result_summary_parts else None,
            interpretation=interpretation,
            action=action,
            clinical_status=clinical_status,
            spec_reference=SPEC_REFERENCE,
            is_placeholder_methodology=False,
            action_grounded_in_spec=action_grounded_in_spec,
            warnings=warnings,
        )
