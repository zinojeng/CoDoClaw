"""
Tier B — WATCH-DM（預測 T2DM 未來約5年 incident HF risk）。

規格出處：OpenClaw for Diabetes HIS.md §6.3——僅列出主要變數（BMI/Age/
SBP/DBP/Creatinine/HDL-C/Fasting plasma glucose/QRS duration/Previous
MI/Previous CABG）與世代分組風險區間示例（最低組約1.1%、最高組約
17.4%），未給出完整回歸係數，屬 Tier B（鐵律2）。

已發表驗證證據：Segar et al. 2019, Diabetes Care（PMID 31519694）——ACCORD
世代推導、ALLHAT 世代外部驗證，C-index 0.70-0.77。2023年系統性回顧/統合
分析（PMID 36898704）：多篇外部驗證 pooled C-statistic 0.70（moderate
certainty）。2024年美國退伍軍人世代重新校正研究（PMID 38328913）：C=0.62，
對高社經剝奪族群系統性低估風險，需依社經剝奪指數重新校正。目前未見
台灣/華人族群外部驗證研究——這是本工具仍屬 Tier B、需本地驗證的原因
之一，不只是因為規格書未給係數。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import ClassVar, Optional

from ...clinical_data_object import ModelProvenance
from ..base import CalculatorTier
from ._base import TierBCalculatorBase

CALCULATOR_ID = "WATCH_DM"
CALCULATOR_VERSION = "v1.0"
SPEC_REFERENCE = "OpenClaw for Diabetes HIS.md §6.3"


@dataclass(frozen=True)
class WatchDmInputs:
    patient_id: str
    as_of: date
    age_years: Optional[float] = None
    bmi: Optional[float] = None
    systolic_bp: Optional[float] = None
    diastolic_bp: Optional[float] = None
    creatinine: Optional[float] = None
    hdl_c: Optional[float] = None
    fasting_plasma_glucose: Optional[float] = None
    qrs_duration_ms: Optional[float] = None
    previous_mi: Optional[bool] = None
    previous_cabg: Optional[bool] = None


class WatchDmCalculator(TierBCalculatorBase):
    calculator_id: ClassVar[str] = CALCULATOR_ID
    calculator_version: ClassVar[str] = CALCULATOR_VERSION
    tier: ClassVar[CalculatorTier] = CalculatorTier.B
    required_inputs: ClassVar[tuple[str, ...]] = (
        "age_years",
        "bmi",
        "systolic_bp",
        "diastolic_bp",
        "creatinine",
        "hdl_c",
        "fasting_plasma_glucose",
        "qrs_duration_ms",
        "previous_mi",
        "previous_cabg",
    )
    spec_reference: ClassVar[str] = SPEC_REFERENCE

    def _model_provenance(self) -> ModelProvenance:
        return ModelProvenance(
            model_name="WATCH-DM Score (Segar et al.)",
            original_population="T2DM 衍生/驗證世代（美國多中心，非台灣族群）",
            taiwan_local_validation_status="not_locally_validated",
            spec_reference=SPEC_REFERENCE + "; §37",
        )
