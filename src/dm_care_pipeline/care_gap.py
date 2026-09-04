"""
【第5站】Care Gap — 依已收案/申報中之照護碼，檢查各自必要檢驗是否齊全，
辨識出「缺漏項目」（care gap）。

直接重用 `dm_eligibility.rules_p14` / `rules_p7` 的 `LabRequirement` 常數與
`state.latest_lab_within()`，不重寫檢驗窗口判斷邏輯（架構文件 5.3節）。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
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
    # ★ 修正（Codex #5）：血脂四項是 4 種各自獨立的檢驗（P14 spec (b)/(c)
    # 逐字：「09001C 總膽固醇、09004C 三酸甘油脂、09043C HDL、09044C
    # LDL」），彼此不互為替代——先前誤放進同一個 LabRequirement.alternatives
    # tuple，讓 latest_lab_within() 把它們當成「任一項即滿足」，導致只做了
    # 總膽固醇就會讓「血脂四項」整組被判定為已完成。改為 4 條各自獨立的
    # LabRequirement，缺任何一項都會被列為 care gap。
    LabRequirement(("09001C",), 180, "血脂四項—總膽固醇(TC)"),
    LabRequirement(("09004C",), 180, "血脂四項—三酸甘油脂(TG)"),
    LabRequirement(("09043C",), 180, "血脂四項—高密度脂蛋白(HDL-C)"),
    LabRequirement(("09044C",), 180, "血脂四項—低密度脂蛋白(LDL-C)"),
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
    # ★ 修正（Codex #6）：記錄這筆缺漏是由哪個/哪些照護碼（或品質監測偽代碼
    # _QUALITY_MONITORING_PSEUDO_CODE）的登記產生。先前下游（見
    # guideline_recommendation._nhi_ckd_p4p_lab_gap_matcher）只能用
    # source_codes（純檢驗項目代碼）去猜這筆缺漏屬於哪個照護碼，但 P14 與
    # P7 的必要檢驗項目代碼高度重疊（如 09006C/12111C 兩邊都要），純靠
    # 檢驗代碼比對會把 P14 品質監測缺漏誤標成 P7 缺漏。owning_codes 才是
    # 唯一權威來源；預設空 tuple 僅供直接建構 CareGapItem 的既有測試相容。
    owning_codes: tuple[str, ...] = ()


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
    state,
    as_of: date,
    requirements: tuple[LabRequirement, ...],
    cfg: EligibilityConfig,
    spec_reference: str,
    owning_code: str,
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
                owning_codes=(owning_code,),
            )
        )
    return items


def _quality_monitoring_triggered(state, as_of: date) -> bool:
    """★ 修正（Codex #4）：品質監測（180天強制檢驗排程）是規格書明文的
    獨立平行規則——出處 `dm_eligibility.rules_p14.check_quality_monitoring()`
    docstring 逐字：「只要當次ICD10=E08-E13+開立A10藥物，且180天內未執行
    NMRP/HbA1c/Mic-Cr/血脂四項之任一者……」——只有「當次(as_of)確實有一筆
    DM 主/次診斷 + A10 藥物並存的就診」才會觸發。先前本模組無條件在
    `include_quality_monitoring=True`（預設值）時就產生四類強制排程項目，
    導致完全沒有就診紀錄、或當次就診非 DM/未用藥的病人也會收到品質監測
    缺漏。直接重用 dm_eligibility 既有常數與方法，不重新定義一份 DM
    ICD10/藥物前綴判斷（鐵律7）。"""
    today_visit = next((e for e in state.valid_encounters() if e.visit_date == as_of), None)
    if today_visit is None:
        return False
    is_dm_visit = today_visit.has_diagnosis_prefix(rules_p14.DM_ICD10_PREFIXES, primary_only=False)
    has_a10 = today_visit.has_medication_prefix(rules_p14.DM_MEDICATION_ATC_PREFIX)
    return is_dm_visit and has_a10


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
    seen_dedup_keys: dict = {}  # key → deduplicated_missing_items 中的索引
    deduplicated_missing_items: list[CareGapItem] = []

    def _record(code: str, items: list[CareGapItem]) -> None:
        by_code[code] = tuple(items)
        missing = [it for it in items if not it.satisfied]
        if missing:
            unresolved_codes.append(code)
        for it in missing:
            all_missing.append(it)
            key = (tuple(sorted(c.upper() for c in it.source_codes)), it.requirement.max_age_days)
            existing_idx = seen_dedup_keys.get(key)
            if existing_idx is None:
                seen_dedup_keys[key] = len(deduplicated_missing_items)
                deduplicated_missing_items.append(it)
            else:
                # ★ 修正（Codex #6）：同一組檢驗需求被不同照護碼各自登記時
                # （如 09006C 同時是 P1408C 與品質監測的必要項目），不可只
                # 保留先登記者、丟棄它其實也屬於後一個照護碼的事實——否則
                # 下游 owning_codes 比對會漏掉它其實也是那個碼的缺漏。合併
                # owning_codes，而不是靜默捨棄。
                existing = deduplicated_missing_items[existing_idx]
                merged_owning = tuple(sorted(set(existing.owning_codes) | set(it.owning_codes)))
                if merged_owning != existing.owning_codes:
                    deduplicated_missing_items[existing_idx] = replace(existing, owning_codes=merged_owning)

    for code in codes_in_scope:
        entry = CARE_GAP_REGISTRY.get(code)
        if entry is None:
            unregistered_codes.append(code)
            continue
        requirements, spec_reference = entry
        items = _build_items(state, as_of, requirements, cfg, spec_reference, owning_code=code)
        _record(code, items)

    if include_quality_monitoring and _quality_monitoring_triggered(state, as_of):
        items = _build_items(
            state,
            as_of,
            QUALITY_MONITORING_LAB_REQUIREMENTS,
            cfg,
            _QUALITY_MONITORING_SPEC_REFERENCE,
            owning_code=_QUALITY_MONITORING_PSEUDO_CODE,
        )
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
