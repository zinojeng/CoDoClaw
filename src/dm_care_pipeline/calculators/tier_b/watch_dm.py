"""
Tier B — WATCH-DM（預測 T2DM 未來約5年 incident HF risk）。

規格出處：OpenClaw for Diabetes HIS.md §6.3——僅列出主要變數（BMI/Age/
SBP/DBP/Creatinine/HDL-C/Fasting plasma glucose/QRS duration/Previous
MI/Previous CABG）與世代分組風險區間示例（最低組約1.1%、最高組約
17.4%），未給出完整回歸係數，屬 Tier B（鐵律2）。
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
