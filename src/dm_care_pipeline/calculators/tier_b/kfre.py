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

已發表驗證證據：KFRE 是這幾個 Tier B 工具中外部驗證最成熟的一個——已在
多國/多世代廣泛驗證（綜述見 Ooi et al. 2024, PMID 38273788：稱其已被廣泛
外部驗證且優於其他模型）。秘魯世代驗證（PMID 41350644，n=30,031）：
區辨力佳（C-index 0.85-0.88），但校正不佳（2年 O/E ratio 高達1.84），
需重新校正才可用於臨床決策。目前未見台灣本地驗證研究。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import ClassVar, Optional

from ...clinical_data_object import ModelProvenance
from ..base import CalculatorExecutionStatus, CalculatorResult, CalculatorTier
from ..ckd_ga import _a_stage, _g_stage
from ._base import TierBCalculatorBase

CALCULATOR_ID = "KFRE_4VAR"
CALCULATOR_VERSION = "v1.0"
SPEC_REFERENCE = "OpenClaw for Diabetes HIS.md §12"
# KFRE 定義為 CKD 病人的腎衰竭風險預測（規格§12/§35：Kidney Failure Risk
# Equation，前提即為已有腎衰竭風險可估），與 calculators/ckd_ga.py 的
# is_normal 判準一致：G1/G2 且 A1 不符合 KDIGO CKD 定義。
_CKD_NORMAL_G_STAGES = ("G1", "G2")


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

    def compute(self, inputs: Kfre4VarInputs) -> CalculatorResult:
        # ★ 修正（Codex #22）：先前沒有任何適用性判斷，非 CKD 病人（如
        # eGFR/UACR 皆正常之 G1A1/G2A1）也會拿到 KFRE 的
        # REQUIRES_EXTERNAL_VALIDATED_MODEL「待驗證模型」care gap 與
        # KFRE_INFO 資訊揭露，即使 KFRE 本身定義即為 CKD 病人的腎衰竭風險
        # 預測，對非 CKD 病人完全不適用。只在 eGFR/UACR 皆齊備、且換算出
        # 的 KDIGO 分期明確不符合 CKD 定義時才判 NOT_APPLICABLE；任一缺值
        # 時無法判斷適用性，一律走原本的 Tier B 流程（缺值本身會被
        # required_inputs 標記）。
        if inputs.egfr is not None and inputs.uacr is not None:
            g_stage = _g_stage(inputs.egfr)
            a_stage = _a_stage(inputs.uacr)
            is_ckd = not (g_stage in _CKD_NORMAL_G_STAGES and a_stage == "A1")
            if not is_ckd:
                base = super().compute(inputs)
                return replace(
                    base,
                    execution_status=CalculatorExecutionStatus.NOT_APPLICABLE,
                    interpretation=(
                        f"KFRE 適用於已診斷 CKD 之病人（OpenClaw HIS §12），病人 eGFR/UACR 換算 "
                        f"KDIGO 分期為 {g_stage}{a_stage}，不符合 CKD 定義（G1/G2 且 A1），"
                        "不需計算腎衰竭風險。"
                    ),
                    action=None,
                )
        return super().compute(inputs)
