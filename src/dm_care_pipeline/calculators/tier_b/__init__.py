"""Tier B calculators：可插拔介面，皆回傳 `execution_status=
REQUIRES_EXTERNAL_VALIDATED_MODEL`（或路由用的 `NOT_APPLICABLE`），不計算
實際風險數值（鐵律2）。"""

from __future__ import annotations

from ..registry import CalculatorRegistry
from .karter_hypoglycemia import KarterHypoglycemiaCalculator
from .kfre import Kfre4VarCalculator
from .prevent_ascvd import (
    LegacyAscvdPceCalculator,
    PreventCalculator,
    already_in_secondary_prevention,
)
from .watch_dm import WatchDmCalculator

__all__ = [
    "WatchDmCalculator",
    "PreventCalculator",
    "LegacyAscvdPceCalculator",
    "already_in_secondary_prevention",
    "KarterHypoglycemiaCalculator",
    "Kfre4VarCalculator",
    "register_tier_b_calculators",
]


def register_tier_b_calculators(registry: CalculatorRegistry) -> None:
    """對 `registry` 註冊 5 個 Tier B calculator（架構文件v2 3.4節）。"""

    registry.register(WatchDmCalculator(), guideline_reference="OpenClaw HIS §6.3")
    registry.register(PreventCalculator(), guideline_reference="OpenClaw HIS §7")
    registry.register(LegacyAscvdPceCalculator(), guideline_reference="OpenClaw HIS §7")
    registry.register(KarterHypoglycemiaCalculator(), guideline_reference="OpenClaw HIS §9")
    registry.register(Kfre4VarCalculator(), guideline_reference="OpenClaw HIS §12")
