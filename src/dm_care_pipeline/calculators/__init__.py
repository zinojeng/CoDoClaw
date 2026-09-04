"""
Layer 3 — Diabetes Calculator Service（規格§13「未來增加 calculator 只增加
一個 Calculator Module」的具體落地）。

Tier A（6項，規格書逐字公式/切點，鐵律1）：CKD G/A、FIB-4、BNP/NT-proBNP
HF screening、ABI/TBI、IWGDF Foot Risk、ADA Level1 低血糖規則式判斷。

Tier B（5項，可插拔介面，鐵律2，皆回傳 `REQUIRES_EXTERNAL_VALIDATED_MODEL`
而非捏造數值）：WATCH-DM、PREVENT、Legacy ASCVD PCE 2013、Karter
Hypoglycemia、KFRE 4-variable。

`register_default_calculators()` 把 Tier A + Tier B 全部註冊進
`DEFAULT_CALCULATOR_REGISTRY`，供 `pipeline.py` 等呼叫端直接使用；individual
calculator 類別/Inputs dataclass 亦可單獨 import 使用（例如單元測試）。
"""

from __future__ import annotations

from ..clinical_data_object import ClinicalDomain
from .abi_tbi import ABITBICalculator, ABITBIInputs
from .base import (
    Calculator,
    CalculatorExecutionStatus,
    CalculatorInputField,
    CalculatorResult,
    CalculatorTier,
)
from .bnp_hf_screen import NatriureticPeptideHFScreenCalculator, NatriureticPeptideInputs
from .ckd_ga import CKDGACalculator, CKDGAInputs
from .fib4 import FIB4Calculator, FIB4Inputs
from .hypoglycemia_ada_l1 import (
    ADA_MAJOR_HYPO_RISK_FACTORS,
    ADA_OTHER_HYPO_RISK_FACTORS,
    ADAHypoglycemiaLevel1Calculator,
    HypoglycemiaRiskFactorInputs,
)
from .iwgdf_foot import (
    IWGDF_FOLLOWUP_INTERVAL_DAYS,
    IWGDFFootInputs,
    IWGDFFootRiskCalculator,
)
from .registry import (
    CalculatorNotFoundError,
    CalculatorRegistration,
    CalculatorRegistry,
    DEFAULT_CALCULATOR_REGISTRY,
)
from .tier_b import (
    KarterHypoglycemiaCalculator,
    Kfre4VarCalculator,
    LegacyAscvdPceCalculator,
    PreventCalculator,
    WatchDmCalculator,
    already_in_secondary_prevention,
    register_tier_b_calculators,
)

__all__ = [
    "CALCULATOR_ID_TO_DOMAIN",
    "Calculator",
    "CalculatorExecutionStatus",
    "CalculatorInputField",
    "CalculatorResult",
    "CalculatorTier",
    "CalculatorRegistration",
    "CalculatorRegistry",
    "CalculatorNotFoundError",
    "DEFAULT_CALCULATOR_REGISTRY",
    # Tier A
    "CKDGACalculator",
    "CKDGAInputs",
    "FIB4Calculator",
    "FIB4Inputs",
    "NatriureticPeptideHFScreenCalculator",
    "NatriureticPeptideInputs",
    "ABITBICalculator",
    "ABITBIInputs",
    "IWGDFFootRiskCalculator",
    "IWGDFFootInputs",
    "IWGDF_FOLLOWUP_INTERVAL_DAYS",
    "ADAHypoglycemiaLevel1Calculator",
    "HypoglycemiaRiskFactorInputs",
    "ADA_MAJOR_HYPO_RISK_FACTORS",
    "ADA_OTHER_HYPO_RISK_FACTORS",
    # Tier B
    "WatchDmCalculator",
    "PreventCalculator",
    "LegacyAscvdPceCalculator",
    "already_in_secondary_prevention",
    "KarterHypoglycemiaCalculator",
    "Kfre4VarCalculator",
    "register_tier_b_calculators",
    "register_tier_a_calculators",
    "register_default_calculators",
]


# calculator_id → ClinicalDomain 映射（Layer3 calculator 結果 → Layer2
# domain 分組唯一權威來源；`CalculatorResult` 本身刻意不帶 domain 欄位，避免
# `calculators/base.py` 依賴 `clinical_state.py` 造成循環耦合，改由消費端
# `clinical_state.derive_clinical_state()` import 本字典使用，不重複宣告
# 各 calculator_id 對照，鐵律7）。
# ★ 工程規則化詮釋，非規格書逐字條文——規格書只逐一描述每個 calculator
# 對應哪個臨床情境，未明文給出「這個 calculator 掛在哪個 ClinicalDomain
# 分組底下顯示」的對照表，需臨床/UI 端覆核。
CALCULATOR_ID_TO_DOMAIN: dict[str, ClinicalDomain] = {
    # Tier A
    "KDIGO_GA": ClinicalDomain.KIDNEY,
    "FIB4": ClinicalDomain.LIVER,
    "BNP_NTPROBNP_HF_SCREEN": ClinicalDomain.HEART_FAILURE,
    "ABI_TBI_PAD_SCREEN": ClinicalDomain.PAD,
    "IWGDF_FOOT_RISK": ClinicalDomain.FOOT,
    "ADA_HYPO_L1": ClinicalDomain.HYPOGLYCEMIA,
    # Tier B
    "WATCH_DM": ClinicalDomain.HEART_FAILURE,
    "PREVENT": ClinicalDomain.ASCVD,
    "ASCVD_PCE_2013": ClinicalDomain.ASCVD,
    "KARTER_HYPO_ED_HOSP": ClinicalDomain.HYPOGLYCEMIA,
    "KFRE_4VAR": ClinicalDomain.KIDNEY,
}


def register_tier_a_calculators(registry: CalculatorRegistry) -> None:
    """對 `registry` 註冊 6 個 Tier A calculator。與 `tier_b.
    register_tier_b_calculators()` 對稱（架構文件v2 未強制規定此函式名稱，
    本檔案為呼叫端便利性新增，非規格條文）。"""

    registry.register(CKDGACalculator(), guideline_reference="OpenClaw HIS §6.1")
    registry.register(FIB4Calculator(), guideline_reference="OpenClaw HIS §6.2")
    registry.register(NatriureticPeptideHFScreenCalculator(), guideline_reference="OpenClaw HIS §6.4")
    registry.register(ABITBICalculator(), guideline_reference="OpenClaw HIS §6.5")
    registry.register(IWGDFFootRiskCalculator(), guideline_reference="OpenClaw HIS §10")
    registry.register(ADAHypoglycemiaLevel1Calculator(), guideline_reference="OpenClaw HIS §8")


def register_default_calculators(registry: CalculatorRegistry | None = None) -> CalculatorRegistry:
    """便利函式：把 Tier A + Tier B 全部註冊進 `registry`（預設
    `DEFAULT_CALCULATOR_REGISTRY`），回傳該 registry。呼叫端（`pipeline.py`
    等）可直接 `from dm_care_pipeline.calculators import
    register_default_calculators`。"""

    target = registry if registry is not None else DEFAULT_CALCULATOR_REGISTRY
    register_tier_a_calculators(target)
    register_tier_b_calculators(target)
    return target
