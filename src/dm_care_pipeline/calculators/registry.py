"""
Calculator Registry — 規格§35/§36「版本控制格式」與「Audit Trail 需可完整
溯源用哪一版公式」的具體落地。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .base import Calculator, CalculatorResult, CalculatorTier

__all__ = [
    "CalculatorRegistration",
    "CalculatorRegistry",
    "CalculatorNotFoundError",
    "DEFAULT_CALCULATOR_REGISTRY",
]


@dataclass(frozen=True)
class CalculatorRegistration:
    calculator_id: str
    version: str
    tier: CalculatorTier
    instance: Calculator
    guideline_reference: Optional[str] = None

    @property
    def qualified_key(self) -> str:
        return f"calculator/{self.calculator_id}/{self.version}"  # 逐字對映規格§35版本控制格式範例


class CalculatorNotFoundError(KeyError):
    """指定的 calculator_id（+version）未在 registry 中註冊。"""


class CalculatorRegistry:
    """`calculator_id` → 該 calculator 所有已註冊版本的登錄表。同一
    `calculator_id` 可同時登錄多個版本（規格§36 Audit Trail 需求：舊病歷用
    舊版計算工具算出的結果，仍須可溯源當時用的是哪一版）。

    open_question（採納 calculators_tier_b 的提問，未裁定，見架構文件v2
    第5節#9）：`get(calculator_id, version=None)` 允許隱含取 latest；規格§36
    要求「用哪一版公式」需完整可溯。本檔案建議呼叫端一律傳入明確
    `version`，但未在型別層面強制禁止隱含 latest。
    """

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str], CalculatorRegistration] = {}
        self._latest_version: dict[str, str] = {}

    def register(
        self,
        calculator: Calculator,
        *,
        guideline_reference: Optional[str] = None,
        is_latest: bool = True,
    ) -> None:
        calculator_id = calculator.calculator_id
        version = calculator.calculator_version
        registration = CalculatorRegistration(
            calculator_id=calculator_id,
            version=version,
            tier=calculator.tier,
            instance=calculator,
            guideline_reference=guideline_reference,
        )
        self._by_key[(calculator_id, version)] = registration
        if is_latest or calculator_id not in self._latest_version:
            self._latest_version[calculator_id] = version

    def _resolve_version(self, calculator_id: str, version: Optional[str]) -> str:
        if version is not None:
            return version
        latest = self._latest_version.get(calculator_id)
        if latest is None:
            raise CalculatorNotFoundError(f"calculator_id={calculator_id!r} 未註冊任何版本")
        return latest

    def get_registration(self, calculator_id: str, version: Optional[str] = None) -> CalculatorRegistration:
        resolved_version = self._resolve_version(calculator_id, version)
        key = (calculator_id, resolved_version)
        registration = self._by_key.get(key)
        if registration is None:
            raise CalculatorNotFoundError(
                f"calculator_id={calculator_id!r} version={resolved_version!r} 未註冊"
            )
        return registration

    def get(self, calculator_id: str, version: Optional[str] = None) -> Calculator:
        return self.get_registration(calculator_id, version).instance

    def get_by_qualified_key(self, qualified_key: str) -> Calculator:
        parts = qualified_key.split("/")
        if len(parts) != 3 or parts[0] != "calculator":
            raise CalculatorNotFoundError(
                f"qualified_key 格式錯誤，應為 'calculator/<id>/<version>'，收到: {qualified_key!r}"
            )
        _, calculator_id, version = parts
        return self.get(calculator_id, version)

    def list_calculators(self, tier: Optional[CalculatorTier] = None) -> tuple[CalculatorRegistration, ...]:
        registrations = list(self._by_key.values())
        if tier is not None:
            registrations = [r for r in registrations if r.tier == tier]
        registrations.sort(key=lambda r: (r.calculator_id, r.version))
        return tuple(registrations)

    def list_calculator_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._latest_version.keys()))

    def compute(self, calculator_id: str, inputs, *, version: Optional[str] = None) -> CalculatorResult:
        registration = self.get_registration(calculator_id, version)
        result = registration.instance.compute(inputs)
        # ★ 修正（Codex 審閱發現）：`CalculatorResult.__post_init__` 只驗證
        # 單一 result 物件內部的欄位一致性（例如 Tier B 不可 COMPUTED），
        # 但沒有任何地方驗證「這個 calculator 實際回傳的 result 真的是它
        # 自己」——一個寫壞或惡意的插件實作可以把
        # `calculator_id`/`tier`/`patient_id` 填成別的值，讓下游（
        # `clinical_state.py`/`guideline_recommendation.py`/
        # `pre_visit_brief.py`）誤把一個假冒的 Tier A COMPUTED 結果當真
        # （鐵律2的「不得偽造已驗證數值」防線若只靠單一物件自我驗證，
        # 對這種身份偽造無效）。registry 作為所有 calculator 呼叫的
        # 唯一入口，在此對回傳身份做交叉驗證。
        if result.calculator_id != registration.calculator_id:
            raise ValueError(
                f"calculator_id={registration.calculator_id!r} 的實作回傳了不一致的 "
                f"CalculatorResult.calculator_id={result.calculator_id!r}（可能是插件實作錯誤，拒絕信任此結果）"
            )
        if result.tier != registration.tier:
            raise ValueError(
                f"calculator_id={registration.calculator_id!r} 註冊為 tier={registration.tier.value}，"
                f"但實作回傳 CalculatorResult.tier={result.tier.value}（可能是插件實作錯誤，拒絕信任此結果）"
            )
        if result.calculator_version != registration.version:
            # ★ 修正（Codex 審閱發現）：先前只驗證 id/tier/patient_id，
            # 未驗證版本——規格§35/§36 版本控制/Audit Trail 的核心需求正是
            # 「舊病歷用舊版公式算出的結果，需可完整追溯是哪一版」，若
            # 呼叫端要求 version="v1.0" 卻拿到一個自稱 "v2.0" 算出的結果，
            # 審計追溯會直接失真。
            raise ValueError(
                f"calculator_id={registration.calculator_id!r} 要求 version={registration.version!r}，"
                f"但實作回傳 CalculatorResult.calculator_version={result.calculator_version!r}"
                "（可能是插件實作錯誤，拒絕信任此結果）"
            )
        expected_patient_id = getattr(inputs, "patient_id", None)
        if expected_patient_id is not None and result.patient_id != expected_patient_id:
            raise ValueError(
                f"calculator_id={registration.calculator_id!r}：輸入 patient_id={expected_patient_id!r} 與 "
                f"回傳 CalculatorResult.patient_id={result.patient_id!r} 不一致（可能是插件實作錯誤，拒絕信任此結果）"
            )
        return result


DEFAULT_CALCULATOR_REGISTRY = CalculatorRegistry()
