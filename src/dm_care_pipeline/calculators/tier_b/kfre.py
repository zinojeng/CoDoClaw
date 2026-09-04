"""
Tier B — 4-variable Kidney Failure Risk Equation (KFRE)。

規格出處：OpenClaw for Diabetes HIS.md §12——4-variable KFRE 需要 Age/
Sex/eGFR/UACR，可預測 2-year 與 5-year kidney failure risk；只列變數，
未給出完整回歸係數，屬 Tier B。

`sex` 消費 `PatientClinicalProfile.sex`（架構文件v2 3.1節新增欄位，已落地於
`pipeline_models.py`；`pipeline.run_stages_1_to_7()` 已接受 `sex` 參數並
透過 `_build_kfre_inputs()` 組裝。生理性別 vs 病歷登記性別的定義本身仍待
臨床/倫理端裁定，見架構文件v2 第5節 open_questions#4）。

calculator_id 採用 `KFRE_4VAR`（比規格§35 範例 `calculator/KFRE/v1` 多了
variant 後綴，因規格書外部已知另有 6/8-variable KFRE，見架構文件v2 第5節
open_questions#10，未最終裁定）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import ClassVar, Optional

from ...clinical_data_object import ModelProvenance
from ..base import CalculatorTier
from ._base import TierBCalculatorBase

CALCULATOR_ID = "KFRE_4VAR"
CALCULATOR_VERSION = "v1.0"
SPEC_REFERENCE = "OpenClaw for Diabetes HIS.md §12"


@dataclass(frozen=True)
class Kfre4VarInputs:
    patient_id: str
    as_of: date
    age_years: Optional[float] = None
    sex: Optional[str] = None
    egfr: Optional[float] = None
    uacr: Optional[float] = None


class Kfre4VarCalculator(TierBCalculatorBase):
    calculator_id: ClassVar[str] = CALCULATOR_ID
    calculator_version: ClassVar[str] = CALCULATOR_VERSION
    tier: ClassVar[CalculatorTier] = CalculatorTier.B
    required_inputs: ClassVar[tuple[str, ...]] = ("age_years", "sex", "egfr", "uacr")
    spec_reference: ClassVar[str] = SPEC_REFERENCE

    def _model_provenance(self) -> ModelProvenance:
        return ModelProvenance(
            model_name="4-variable Kidney Failure Risk Equation (KFRE)",
            original_population="多國 CKD 世代（含北美/歐洲/亞洲，非專門以台灣族群建立）",
            taiwan_local_validation_status="not_locally_validated",
            spec_reference=SPEC_REFERENCE + "; §37",
        )
