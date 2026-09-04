"""
【第5站】Care Gap — 依已收案/申報中之照護碼，檢查各自必要檢驗是否齊全，
辨識出「缺漏項目」（care gap）。

直接重用 `dm_eligibility.rules_p14` / `rules_p7` 的 `LabRequirement` 常數與
`state.latest_lab_within()`，不重寫檢驗窗口判斷邏輯（架構文件 5.3節）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Sequence

from dm_eligibility import rules_p14, rules_p7
from dm_eligibility.models import EligibilityConfig, LabRequirement, LabResult

from .pipeline_models import PatientClinicalProfile

# 照護碼 → (必要檢驗清單, 規格出處字串)。出處字串供 Guideline 階段的
# spec_reference 溯源使用（整合新增需求，見架構文件3.5節）。
CARE_GAP_REGISTRY: dict[str, tuple[tuple[LabRequirement, ...], str]] = {
    "P1407C": (rules_p14.P1407_LAB_REQUIREMENTS_BASE, "P14 spec (b) B.2 附表8.2.1"),
    "P1408C": (rules_p14.P1408_LAB_REQUIREMENTS_BASE, "P14 spec (b) B.3 附表8.2.2"),
    "P1409C": (rules_p14.P1409_LAB_REQUIREMENTS_BASE, "P14 spec (b) B.4 附表8.2.3"),
    "P7001C": (rules_p7.P7001_LAB_REQUIREMENTS_BASE, "P7 spec (d)"),
    "P7002C": (rules_p7.P7002_LAB_REQUIREMENTS_BASE, "P7 spec (d)"),
    # TODO：P1410C/P1411C/P4301C/P4302C/P4303C/P7003C 規格書逐字擷取未見
    # 完整必要檢驗清單，刻意留空；查無對應 code 一律進 unregistered_codes
    # 顯式回報，不可靜默視為「無缺漏」（見架構文件第8節#5）。
}

# 品質監測（180天強制檢驗排程）之四項必要檢驗。原始邏輯內嵌於
# rules_p14.check_quality_monitoring() 的區域變數，架構文件建議
# rules_p14.py 未來抽成具名常數 export（非破壞性重構，見架構文件5.6節），
# 在該重構落地前，本模組先在此重申同一份定義，維持與 dm_eligibility 行為一致。
# TODO（技術債，非本模組可單方面解決）：一旦 rules_p14.py 抽出對應具名
# 常數，這裡應改為直接 import，避免兩處維護同一份 180 天四項清單。
QUALITY_MONITORING_LAB_REQUIREMENTS: tuple[LabRequirement, ...] = (
    LabRequirement(("23501C", "23502C"), 180, "NMRP(眼底檢查)"),
    LabRequirement(("09006C",), 180, "HbA1c"),
    LabRequirement(("12111C",), 180, "Mic-Cr(微量白蛋白)"),
    LabRequirement(("09001C", "09004C", "09043C", "09044C"), 180, "血脂四項"),
)
_QUALITY_MONITORING_PSEUDO_CODE = "__QUALITY_MONITORING__"
_QUALITY_MONITORING_SPEC_REFERENCE = "P14 spec (c)(d) 品質監測180天強制檢驗排程"


@dataclass(frozen=True)
class CareGapItem:
    requirement: LabRequirement
    satisfied: bool
    most_recent_within_window: LabResult | None
    most_recent_ever: LabResult | None
    days_since_last: int | None
    source_codes: tuple[str, ...]
    spec_reference: str  # 整合新增：滿足 Guideline 階段「拒絕消費無出處證據」的要求


@dataclass
class CareGapReport:
    patient_id: str
    as_of_date: date
    by_code: dict[str, tuple[CareGapItem, ...]]
    unresolved_codes: list[str]
    deduplicated_missing_items: list[CareGapItem]
    unregistered_codes: list[str]
    warnings: list[str] = field(default_factory=list)


def _build_items(
    state, as_of: date, requirements: tuple[LabRequirement, ...], cfg: EligibilityConfig, spec_reference: str
) -> list[CareGapItem]:
    reqs_with_ga = rules_p14._with_ga_substitute(requirements, cfg)
    items: list[CareGapItem] = []
    for req in reqs_with_ga:
        alt_upper = {c.upper() for c in req.alternatives}
        within = state.latest_lab_within(req.alternatives, as_of, req.max_age_days)
        # 鐵律5：與 within 一致，未來日期的檢驗結果不代表「已知」資訊——否則
        # 會產生負的 days_since_last、「已逾期」卻其實是未來日期的假證據。
        ever_candidates = [
            lr for lr in state.lab_results if lr.item_code.upper() in alt_upper and lr.result_date <= as_of
        ]
        most_recent_ever = max(ever_candidates, key=lambda lr: lr.result_date) if ever_candidates else None
        days_since = (as_of - most_recent_ever.result_date).days if most_recent_ever else None
        items.append(
            CareGapItem(
                requirement=req,
                satisfied=within is not None,
                most_recent_within_window=within,
                most_recent_ever=most_recent_ever,
                days_since_last=days_since,
                source_codes=req.alternatives,
                spec_reference=spec_reference,
            )
        )
    return items


def assess_care_gaps(
    profile: PatientClinicalProfile,
    codes_in_scope: Sequence[str],
    config: EligibilityConfig | None = None,
    include_quality_monitoring: bool = True,
) -> CareGapReport:
    """`codes_in_scope` 由呼叫端決定（典型作法：取
    `profile.eligibility_report.eligible_codes()` 中已收案/緊接著要申報的
    代碼）。`config` 直接重用 `EligibilityConfig`，確保 GA 替代規則與
    dm_eligibility 收案引擎完全一致。"""
    cfg = config or EligibilityConfig()
    state = profile.enrollment_state
    as_of = profile.as_of_date

    by_code: dict[str, tuple[CareGapItem, ...]] = {}
    unresolved_codes: list[str] = []
    unregistered_codes: list[str] = []
    all_missing: list[CareGapItem] = []
    warnings: list[str] = []
    seen_dedup_keys: set = set()
    deduplicated_missing_items: list[CareGapItem] = []

    def _record(code: str, items: list[CareGapItem]) -> None:
        by_code[code] = tuple(items)
        missing = [it for it in items if not it.satisfied]
        if missing:
            unresolved_codes.append(code)
        for it in missing:
            all_missing.append(it)
            key = (tuple(sorted(c.upper() for c in it.source_codes)), it.requirement.max_age_days)
            if key not in seen_dedup_keys:
                seen_dedup_keys.add(key)
                deduplicated_missing_items.append(it)

    for code in codes_in_scope:
        entry = CARE_GAP_REGISTRY.get(code)
        if entry is None:
            unregistered_codes.append(code)
            continue
        requirements, spec_reference = entry
        items = _build_items(state, as_of, requirements, cfg, spec_reference)
        _record(code, items)

    if include_quality_monitoring:
        items = _build_items(state, as_of, QUALITY_MONITORING_LAB_REQUIREMENTS, cfg, _QUALITY_MONITORING_SPEC_REFERENCE)
        _record(_QUALITY_MONITORING_PSEUDO_CODE, items)

    if unregistered_codes:
        warnings.append(
            f"以下代碼未登記於 CARE_GAP_REGISTRY，無法判斷 Care Gap（需向健保署或院內文件補齊必要檢驗清單後註冊）: "
            f"{', '.join(unregistered_codes)}"
        )

    return CareGapReport(
        patient_id=profile.patient_id,
        as_of_date=as_of,
        by_code=by_code,
        unresolved_codes=unresolved_codes,
        deduplicated_missing_items=deduplicated_missing_items,
        unregistered_codes=unregistered_codes,
        warnings=warnings,
    )
