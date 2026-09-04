"""
Tier B — PREVENT / Legacy ASCVD PCE Risk Engine。

規格出處：OpenClaw for Diabetes HIS.md §7——
    - Secondary prevention：已有 MI/ACS/stroke/PAD/revascularization
      者直接進入 established ASCVD pathway，不需要 primary prevention
      calculator 決定是否高風險（本檔案 `already_in_secondary_prevention()`
      逐字對應此路由規則）。
    - Primary prevention：AHA PREVENT，適用 30-79 歲且無已知 CVD 之成人，
      估計 10-year/30-year CVD risk；只列變數與適用範圍，無完整係數，
      屬 Tier B。
    - Legacy 10-year ASCVD（Pooled Cohort Equations, 2013）：第一版建議
      同時保留，供不同 guideline/workflow 使用；同樣只有工具名稱，無完整
      係數，屬 Tier B。`race_ethnicity` 是否納入為倫理待裁定項（見架構
      文件v2 第5節 open_questions#5）。

已發表驗證證據：
    - PREVENT：2026年美國30家醫療系統68萬人EHR外部驗證（Khan et al.,
      PMID 42554063）：校正良好，C-index 0.77-0.82。2026年中國 CHARLS
      世代驗證（Fan et al., PMID 42393162，n=10,068）：AUC 僅0.61-0.62
      （遠低於美國驗證的0.76-0.79），校正斜率0.47-0.51——需大幅重新校正
      才能用於華人族群，是本工具屬 Tier B 的實證依據之一。
    - Legacy PCE：十年來被外部驗證數十次，多數研究顯示系統性高估風險
      （SPRINT 世代驗證 PMID 31088530；PURE-Colombia PMID 39041366）。
      中國世代驗證（China-PAR, Yang et al. 2016, PMID 27682885）明確指出
      PCE 校正 χ² 遠高於本土方程式；新疆維吾爾/哈薩克世代驗證
      （PMID 32993590）同樣顯示需重新校正才可用於華人族群。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from typing import ClassVar, Optional

from ...clinical_data_object import ModelProvenance
from ..base import (
    CalculatorExecutionStatus,
    CalculatorResult,
    CalculatorTier,
)
from ._base import TierBCalculatorBase

PREVENT_SPEC_REFERENCE = "OpenClaw for Diabetes HIS.md §7"
LEGACY_ASCVD_SPEC_REFERENCE = "OpenClaw for Diabetes HIS.md §7（legacy transition layer）"

# §7 明文列出之 secondary prevention 判定依據類別（重用既有
# COMPLICATION_ICD10_PREFIXES 的 key，鐵律7：不重複硬編一份 ICD 前綴表）。
SECONDARY_PREVENTION_COMPLICATION_CATEGORIES: frozenset[str] = frozenset({"CVD", "CEREBROVASCULAR", "PVD"})

PREVENT_MIN_AGE_YEARS = 30
PREVENT_MAX_AGE_YEARS = 79
# Legacy Pooled Cohort Equations 慣例引用之適用年齡範圍（非規格書逐字條文，
# 是外部已知 PCE 慣例，需臨床端確認，見架構文件v2 3.4節/第5節 open_questions）。
LEGACY_ASCVD_MIN_AGE_YEARS = 40
LEGACY_ASCVD_MAX_AGE_YEARS = 79


def already_in_secondary_prevention(
    complications: frozenset[str], has_revascularization_history: Optional[bool] = None
) -> bool:
    """§7 逐字文字路由規則，非風險計算，不受 Tier B 限制。命中既有
    `COMPLICATION_ICD10_PREFIXES` 的 CVD/CEREBROVASCULAR/PVD 類別，或曾有
    血管重建史，即視為 secondary prevention。"""

    return bool(complications & SECONDARY_PREVENTION_COMPLICATION_CATEGORIES) or bool(has_revascularization_history)


@dataclass(frozen=True)
class PreventInputs:
    patient_id: str
    as_of: date
    age_years: Optional[float] = None
    systolic_bp: Optional[float] = None
    total_cholesterol: Optional[float] = None
    hdl_c: Optional[float] = None
    current_statin_treatment: Optional[bool] = None
    smoking_status: Optional[str] = None
    egfr: Optional[float] = None
    bmi: Optional[float] = None
    diabetes_status: Optional[bool] = None
    uacr: Optional[float] = None
    hba1c_latest: Optional[float] = None
    social_deprivation_index: Optional[float] = None
    complications: frozenset[str] = field(default_factory=frozenset)
    has_revascularization_history: Optional[bool] = None


class PreventCalculator(TierBCalculatorBase):
    calculator_id: ClassVar[str] = "PREVENT"
    calculator_version: ClassVar[str] = "v1.0"
    tier: ClassVar[CalculatorTier] = CalculatorTier.B
    required_inputs: ClassVar[tuple[str, ...]] = (
        "systolic_bp",
        "total_cholesterol",
        "hdl_c",
        "current_statin_treatment",
        "smoking_status",
        "egfr",
        "bmi",
        "diabetes_status",
    )
    spec_reference: ClassVar[str] = PREVENT_SPEC_REFERENCE

    def _model_provenance(self) -> ModelProvenance:
        return ModelProvenance(
            model_name="AHA PREVENT",
            original_population="約650萬美國成人（規格§37明載）",
            taiwan_local_validation_status="not_locally_validated",
            spec_reference=PREVENT_SPEC_REFERENCE + "; §37",
        )

    def _extract_field_values(self, inputs: PreventInputs) -> dict[str, object]:
        # complications/has_revascularization_history 是路由用內部變數，
        # 不是 PREVENT 模型本身的計算變數，故不列入 CalculatorInputField。
        values = super()._extract_field_values(inputs)
        values.pop("complications", None)
        values.pop("has_revascularization_history", None)
        return values

    def compute(self, inputs: PreventInputs) -> CalculatorResult:
        if already_in_secondary_prevention(inputs.complications, inputs.has_revascularization_history):
            base = super().compute(inputs)
            return replace(
                base,
                execution_status=CalculatorExecutionStatus.NOT_APPLICABLE,
                interpretation=(
                    "病人已符合 secondary prevention（既有 CVD/腦血管/PVD 診斷或血管重建史），"
                    "不需以 primary prevention calculator（PREVENT）判斷風險，"
                    "應直接進入 established ASCVD pathway（OpenClaw HIS §7）。"
                ),
                action=None,
            )
        if inputs.age_years is not None and not (PREVENT_MIN_AGE_YEARS <= inputs.age_years <= PREVENT_MAX_AGE_YEARS):
            base = super().compute(inputs)
            return replace(
                base,
                execution_status=CalculatorExecutionStatus.NOT_APPLICABLE,
                interpretation=(
                    f"PREVENT 適用範圍為 {PREVENT_MIN_AGE_YEARS}-{PREVENT_MAX_AGE_YEARS} 歲"
                    f"（OpenClaw HIS §7），病人年齡={inputs.age_years} 不在範圍內。"
                ),
                action=None,
            )
        return super().compute(inputs)


@dataclass(frozen=True)
class LegacyAscvdPceInputs:
    patient_id: str
    as_of: date
    age_years: Optional[float] = None
    systolic_bp: Optional[float] = None
    total_cholesterol: Optional[float] = None
    hdl_c: Optional[float] = None
    current_statin_treatment: Optional[bool] = None
    smoking_status: Optional[str] = None
    egfr: Optional[float] = None
    bmi: Optional[float] = None
    diabetes_status: Optional[bool] = None
    treated_hypertension: Optional[bool] = None
    race_ethnicity: Optional[str] = None  # ★ 倫理待裁定項，見架構文件v2 第5節#5
    complications: frozenset[str] = field(default_factory=frozenset)
    has_revascularization_history: Optional[bool] = None


class LegacyAscvdPceCalculator(TierBCalculatorBase):
    calculator_id: ClassVar[str] = "ASCVD_PCE_2013"
    calculator_version: ClassVar[str] = "v1.0"
    tier: ClassVar[CalculatorTier] = CalculatorTier.B
    required_inputs: ClassVar[tuple[str, ...]] = (
        "systolic_bp",
        "total_cholesterol",
        "hdl_c",
        "current_statin_treatment",
        "smoking_status",
        "treated_hypertension",
        "race_ethnicity",
    )
    spec_reference: ClassVar[str] = LEGACY_ASCVD_SPEC_REFERENCE

    def _model_provenance(self) -> ModelProvenance:
        return ModelProvenance(
            model_name="Pooled Cohort Equations (2013 ACC/AHA Legacy ASCVD Risk)",
            original_population="Pooled Cohort Equations 原始衍生世代（美國多中心，非台灣族群）",
            taiwan_local_validation_status="not_locally_validated",
            spec_reference=LEGACY_ASCVD_SPEC_REFERENCE + "; §37",
        )

    def _extract_field_values(self, inputs: LegacyAscvdPceInputs) -> dict[str, object]:
        values = super()._extract_field_values(inputs)
        values.pop("complications", None)
        values.pop("has_revascularization_history", None)
        return values

    def compute(self, inputs: LegacyAscvdPceInputs) -> CalculatorResult:
        if already_in_secondary_prevention(inputs.complications, inputs.has_revascularization_history):
            base = super().compute(inputs)
            return replace(
                base,
                execution_status=CalculatorExecutionStatus.NOT_APPLICABLE,
                interpretation=(
                    "病人已符合 secondary prevention，不需以 primary prevention calculator 判斷風險，"
                    "應直接進入 established ASCVD pathway（OpenClaw HIS §7）。"
                ),
                action=None,
            )
        # TODO：40-79 歲範圍為 Pooled Cohort Equations 外部已知慣例引用，
        # 非規格書逐字條文，需臨床端確認是否採用、以及邊界是否正確。
        if inputs.age_years is not None and not (LEGACY_ASCVD_MIN_AGE_YEARS <= inputs.age_years <= LEGACY_ASCVD_MAX_AGE_YEARS):
            base = super().compute(inputs)
            return replace(
                base,
                execution_status=CalculatorExecutionStatus.NOT_APPLICABLE,
                interpretation=(
                    f"Legacy ASCVD PCE 慣例適用範圍為 {LEGACY_ASCVD_MIN_AGE_YEARS}-{LEGACY_ASCVD_MAX_AGE_YEARS} 歲"
                    f"（外部慣例引用，非規格逐字條文），病人年齡={inputs.age_years} 不在範圍內。"
                ),
                action=None,
            )
        return super().compute(inputs)
