"""
Tier B — Karter Hypoglycemia Risk Stratification Tool（12個月
hypoglycemia-related ED/hospitalization 風險）。

規格出處：OpenClaw for Diabetes HIS.md §9——6-variable EHR-based model：
    1. Previous hypoglycemia-related ED/hospital utilization
    2. ED visits in previous 12 months
    3. Insulin use
    4. Sulfonylurea use
    5. CKD stage 4-5 / severe kidney disease
    6. Age
只列變數與大致分級（<1%/1-5%/>5%），未給出完整計分表，屬 Tier B。

`ckd_stage_4_5_or_severe` 設計為由呼叫端（pipeline 組裝層）消費
`calculators.ckd_ga.KDIGO_GA` 的輸出（`stage in {"G4","G5"}`）算出後傳入，
本檔案不重複硬編 eGFR<30 這條規則（鐵律7）。`ed_visits_prior_12mo`/
`prior_hypo_related_ed_or_hosp` 消費架構文件v2 3.1節新增的
`EncounterUtilizationRecord`——`pipeline.run_stages_1_to_7()` 已接受
`encounter_utilization` 參數並透過 `_build_karter_inputs()` 組裝（過去365天
窗口內 `setting=="ed"` 之筆數/是否曾有低血糖相關 ED/住院），呼叫端未提供
時仍以 None 傳入，不臆測。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import ClassVar, Optional

from ...clinical_data_object import ModelProvenance
from ..base import CalculatorTier
from ._base import TierBCalculatorBase

CALCULATOR_ID = "KARTER_HYPO_ED_HOSP"
CALCULATOR_VERSION = "v1.0"
SPEC_REFERENCE = "OpenClaw for Diabetes HIS.md §9"


@dataclass(frozen=True)
class KarterHypoglycemiaInputs:
    patient_id: str
    as_of: date
    prior_hypo_related_ed_or_hosp: Optional[bool] = None
    ed_visits_prior_12mo: Optional[int] = None
    insulin_use: Optional[bool] = None
    sulfonylurea_use: Optional[bool] = None
    ckd_stage_4_5_or_severe: Optional[bool] = None
    age_years: Optional[float] = None


class KarterHypoglycemiaCalculator(TierBCalculatorBase):
    calculator_id: ClassVar[str] = CALCULATOR_ID
    calculator_version: ClassVar[str] = CALCULATOR_VERSION
    tier: ClassVar[CalculatorTier] = CalculatorTier.B
    required_inputs: ClassVar[tuple[str, ...]] = (
        "prior_hypo_related_ed_or_hosp",
        "ed_visits_prior_12mo",
        "insulin_use",
        "sulfonylurea_use",
        "ckd_stage_4_5_or_severe",
        "age_years",
    )
    spec_reference: ClassVar[str] = SPEC_REFERENCE

    def _model_provenance(self) -> ModelProvenance:
        return ModelProvenance(
            model_name="Karter Hypoglycemia Risk Stratification Tool（6-variable EHR-based）",
            original_population="Karter et al. EHR-based 驗證世代（非台灣族群）",
            taiwan_local_validation_status="not_locally_validated",
            spec_reference=SPEC_REFERENCE + "; §37",
        )
