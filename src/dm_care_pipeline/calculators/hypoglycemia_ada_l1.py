"""
Tier A — ADA Level 1 Clinical Hypoglycemia Risk。

規格 OpenClaw for Diabetes HIS.md §8 給出的是「風險因子清單」（非計分
公式）：先檢查是否使用 insulin/sulfonylurea/meglitinide，再看是否命中
下列 major risk factors：
    - 近 3–6 個月 Level 2/3 hypoglycemia
    - intensive insulin treatment
    - impaired hypoglycemia awareness
    - kidney failure
    - cognitive impairment / dementia
    - history of metabolic surgery
以及其他 risk factors（minor/other）：
    - recurrent Level 1 hypoglycemia / basal insulin / age >=75 /
      high glucose variability / polypharmacy / cardiovascular disease / CKD

★★★ 鐵律1 明文要求的警語 ★★★：本檔案「有相關用藥 AND 至少1項 major
factor → HIGH；有相關用藥且僅有 minor factor → MODERATE；否則 → LOW」
這一組規則，是工程對規格書風險因子清單的**規則化詮釋**，並非規格書逐字
給出的計分演算法，正式上線前需臨床覆核判斷邏輯的排列組合是否恰當。

規格§5 四態並無「中度未來風險」這一層級，因此 MODERATE 情境
`clinical_status` 刻意留 None（不臆造第5態），僅在 `result_summary`/
`interpretation` 中以文字呈現 MODERATE，供 UI 顯示但不進入四態安全分級。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import ClassVar, Optional

from ..clinical_data_object import ClinicalStatus
from .base import (
    CalculatorExecutionStatus,
    CalculatorInputField,
    CalculatorResult,
    CalculatorTier,
)

CALCULATOR_ID = "ADA_HYPO_L1"
CALCULATOR_VERSION = "v1.0"
SPEC_REFERENCE = "OpenClaw for Diabetes HIS.md §8"
GUIDELINE_ID = "ADA_SOC_2026"

# §8 逐字列出的 major risk factors（工程規則化詮釋的分類依據，見模組
# docstring 警語）。
ADA_MAJOR_HYPO_RISK_FACTORS: frozenset[str] = frozenset(
    {
        "recent_level2_or_3_hypoglycemia_3_6mo",
        "intensive_insulin_treatment",
        "impaired_hypoglycemia_awareness",
        "kidney_failure",
        "cognitive_impairment_or_dementia",
        "history_of_metabolic_surgery",
    }
)

# §8 逐字列出的 other/minor risk factors。
ADA_OTHER_HYPO_RISK_FACTORS: frozenset[str] = frozenset(
    {
        "recurrent_level1_hypoglycemia",
        "basal_insulin",
        "age_75_or_older",
        "high_glucose_variability",
        "polypharmacy",
        "cardiovascular_disease",
        "ckd",
    }
)


@dataclass(frozen=True)
class HypoglycemiaRiskFactorInputs:
    patient_id: str
    as_of: date
    on_insulin: Optional[bool] = None
    on_sulfonylurea: Optional[bool] = None
    on_meglitinide: Optional[bool] = None
    major_factors: frozenset[str] = field(default_factory=frozenset)
    minor_factors: frozenset[str] = field(default_factory=frozenset)
    # ★ 具名旗標：空集合本身無法區分「已評估、確認無風險因子」與「尚未
    # 評估風險因子」，比照鐵律6 不靜默假設，呼叫端必須明確傳入
    # `risk_factors_assessed=True` 才代表 major_factors/minor_factors 是
    # 完整評估後的結果（即使結果為空集合）。
    risk_factors_assessed: bool = False


class ADAHypoglycemiaLevel1Calculator:
    calculator_id: ClassVar[str] = CALCULATOR_ID
    calculator_version: ClassVar[str] = CALCULATOR_VERSION
    tier: ClassVar[CalculatorTier] = CalculatorTier.A
    required_inputs: ClassVar[tuple[str, ...]] = ("on_insulin", "on_sulfonylurea", "on_meglitinide")

    def compute(self, inputs: HypoglycemiaRiskFactorInputs) -> CalculatorResult:
        med_fields = {
            "on_insulin": inputs.on_insulin,
            "on_sulfonylurea": inputs.on_sulfonylurea,
            "on_meglitinide": inputs.on_meglitinide,
        }
        input_fields = tuple(
            CalculatorInputField(name=name, provided=value is not None, value=value) for name, value in med_fields.items()
        ) + (
            CalculatorInputField(name="major_factors", provided=True, value=sorted(inputs.major_factors)),
            CalculatorInputField(name="minor_factors", provided=True, value=sorted(inputs.minor_factors)),
            CalculatorInputField(name="risk_factors_assessed", provided=True, value=inputs.risk_factors_assessed),
        )

        unknown_major = inputs.major_factors - ADA_MAJOR_HYPO_RISK_FACTORS
        unknown_minor = inputs.minor_factors - ADA_OTHER_HYPO_RISK_FACTORS
        warnings: list[str] = []
        if unknown_major:
            warnings.append(f"major_factors 含未登記於§8清單之代碼: {sorted(unknown_major)}（仍計入判斷，需藥師/臨床覆核代碼定義）")
        if unknown_minor:
            warnings.append(f"minor_factors 含未登記於§8清單之代碼: {sorted(unknown_minor)}（仍計入判斷，需藥師/臨床覆核代碼定義）")

        if inputs.on_insulin is None and inputs.on_sulfonylurea is None and inputs.on_meglitinide is None:
            return CalculatorResult(
                calculator_id=self.calculator_id,
                calculator_version=self.calculator_version,
                tier=self.tier,
                patient_id=inputs.patient_id,
                computed_at=inputs.as_of,
                execution_status=CalculatorExecutionStatus.INSUFFICIENT_DATA,
                inputs=input_fields,
                missing_inputs=("on_insulin", "on_sulfonylurea", "on_meglitinide"),
                spec_reference=SPEC_REFERENCE,
                warnings=tuple(warnings) + ("用藥資料（insulin/sulfonylurea/meglitinide）皆未知，無法判斷低血糖風險",),
            )

        on_relevant_medication = bool(inputs.on_insulin) or bool(inputs.on_sulfonylurea) or bool(inputs.on_meglitinide)

        if not on_relevant_medication:
            return CalculatorResult(
                calculator_id=self.calculator_id,
                calculator_version=self.calculator_version,
                tier=self.tier,
                patient_id=inputs.patient_id,
                computed_at=inputs.as_of,
                execution_status=CalculatorExecutionStatus.COMPUTED,
                inputs=input_fields,
                missing_inputs=(),
                result_values={"risk_level": "LOW", "matched_major_factors": (), "matched_minor_factors": ()},
                result_summary="Hypoglycemia risk: LOW（未使用 insulin/sulfonylurea/meglitinide）",
                interpretation="LOW",
                clinical_status=None,
                guideline=GUIDELINE_ID,
                spec_reference=SPEC_REFERENCE,
                is_placeholder_methodology=False,
                action_grounded_in_spec=False,
                warnings=tuple(warnings),
            )

        if not inputs.risk_factors_assessed:
            return CalculatorResult(
                calculator_id=self.calculator_id,
                calculator_version=self.calculator_version,
                tier=self.tier,
                patient_id=inputs.patient_id,
                computed_at=inputs.as_of,
                execution_status=CalculatorExecutionStatus.INSUFFICIENT_DATA,
                inputs=input_fields,
                missing_inputs=("risk_factors_assessed",),
                spec_reference=SPEC_REFERENCE,
                warnings=tuple(warnings) + ("病人使用相關降血糖藥物，但風險因子（major/minor factors）尚未完整評估，無法判斷風險等級",),
            )

        if inputs.major_factors:
            action_items = ["review insulin dose", "consider CGM", "review sulfonylurea", "adjust glycemic target", "hypoglycemia education"]
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
                    "risk_level": "HIGH",
                    "matched_major_factors": sorted(inputs.major_factors),
                    "matched_minor_factors": sorted(inputs.minor_factors),
                },
                result_summary=f"Hypoglycemia risk: HIGH；Evidence: {sorted(inputs.major_factors)}",
                interpretation="HIGH",
                action="; ".join(action_items),
                clinical_status=ClinicalStatus.HIGH_RISK,
                guideline=GUIDELINE_ID,
                spec_reference=SPEC_REFERENCE,
                is_placeholder_methodology=False,
                action_grounded_in_spec=True,
                warnings=tuple(warnings),
            )

        if inputs.minor_factors:
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
                    "risk_level": "MODERATE",
                    "matched_major_factors": (),
                    "matched_minor_factors": sorted(inputs.minor_factors),
                },
                result_summary=f"Hypoglycemia risk: MODERATE；Evidence: {sorted(inputs.minor_factors)}",
                interpretation="MODERATE",
                clinical_status=None,  # §5四態無「中度未來風險」層級，不臆造第5態
                guideline=GUIDELINE_ID,
                spec_reference=SPEC_REFERENCE,
                is_placeholder_methodology=False,
                action_grounded_in_spec=True,
                warnings=tuple(warnings),
            )

        return CalculatorResult(
            calculator_id=self.calculator_id,
            calculator_version=self.calculator_version,
            tier=self.tier,
            patient_id=inputs.patient_id,
            computed_at=inputs.as_of,
            execution_status=CalculatorExecutionStatus.COMPUTED,
            inputs=input_fields,
            missing_inputs=(),
            result_values={"risk_level": "LOW", "matched_major_factors": (), "matched_minor_factors": ()},
            result_summary="Hypoglycemia risk: LOW（有相關用藥，但未命中任何已評估之風險因子）",
            interpretation="LOW",
            clinical_status=None,
            guideline=GUIDELINE_ID,
            spec_reference=SPEC_REFERENCE,
            is_placeholder_methodology=False,
            action_grounded_in_spec=False,
            warnings=tuple(warnings),
        )
