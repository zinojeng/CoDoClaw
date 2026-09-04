"""
【第9站】後續追蹤 — 推算下一次應回診/複查日期，並生成「下一輪
PatientEnrollmentState」可直接使用的 `next_recommended_visit_date`，
串接回 dm_eligibility 的 `EligibilityEngine.evaluate()` 形成封閉迴圈。

重用 `rules_p14.check_stage2_entry_eligible()`、`rules_p7.
P7001_FIRST_INTERVAL_DAYS`/`P7001_SUBSEQUENT_INTERVAL_DAYS`、
`state.last_claim_date()`/`first_claim_date()` 等既有輔助方法計算各照護碼
的「下次到期日」，不重新實作 rules_p14/rules_p7 完整的資格判斷邏輯（例如
不重新檢查檢驗齊全度/年齡/診別排除等——這些屬於「當次是否可以申報」的
判斷，本站要回答的是「下次何時應該回診」，兩者不同）。若因規格書未明訂
基準（例如 P1410C 第1次間隔）而無法推算，一律記入 warnings，不可靜默
假設。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal, Optional, Protocol

from dm_eligibility import rules_p14, rules_p7
from dm_eligibility.models import EligibilityConfig

from .alert import AlertLevel
from .complication_identification import ComplicationReport
from .physician_decision import PhysicianDecisionRecord
from .pipeline_models import PatientClinicalProfile
from .guideline_recommendation import RecommendationPriority

# 重申 rules_p14.py 函式內文之相同數值（規格書明文十週間隔，P14 spec
# Q1/rules_p14.py 現行實作）。技術債：rules_p14.py 現況把它寫成函式內
# 字面常數而非 export 的具名常數，建議後續重構時抽出並改由本模組直接
# import，避免兩處維護（見架構文件5.6節、8節#9）。
SUBSEQUENT_TRACKING_INTERVAL_DAYS = 70

# 醫師接受「URGENT」優先度建議後，工程佔位之最長回診間隔上限（非規格書
# 條文，屬工程實作合理假設，需臨床端確認合理值）。
_URGENT_FOLLOWUP_CAP_DAYS = 30


@dataclass(frozen=True)
class MonitoringItem:
    item_code: str
    description: str
    interval_days: int
    due_date: date
    source: str
    is_placeholder_interval: bool


@dataclass
class ComplicationMonitoringRule:
    category: str  # COMPLICATION_ICD10_PREFIXES 的 key
    item_code: str
    description: str
    interval_days: int
    is_placeholder_interval: bool


@dataclass
class ComplicationMonitoringConfig:
    rules: tuple[ComplicationMonitoringRule, ...] = field(
        default_factory=lambda: (
            ComplicationMonitoringRule("NEPHROPATHY", "FOOT_EXAM", "足部檢查", 90, True),  # TODO placeholder
            ComplicationMonitoringRule("NEPHROPATHY", "CKD_REEVAL", "CKD分期複評", 365, True),  # TODO placeholder
        )
    )
    fallback_visit_interval_days: int = 90  # TODO：無法推算到期日時的保守回診間隔，實際值待臨床端訂定


@dataclass
class FollowUpPlan:
    patient_id: str
    current_as_of_date: date
    next_recommended_visit_date: date
    next_code_due_dates: dict[str, Optional[date]]
    monitoring_items: list[MonitoringItem]
    reasons: list[str]
    warnings: list[str]


def _p1408_due_date(state, cfg: EligibilityConfig, as_of: date, eligible_today: bool) -> Optional[date]:
    if not state.has_claim("P1407C"):
        return None
    if eligible_today:
        return as_of + timedelta(days=SUBSEQUENT_TRACKING_INTERVAL_DAYS)
    last = state.last_claim_date("P1408C")
    if last is not None:
        return last + timedelta(days=SUBSEQUENT_TRACKING_INTERVAL_DAYS)
    p1407_date = state.first_claim_date("P1407C")
    if p1407_date is None:
        return None
    return p1407_date + timedelta(days=cfg.first_p1408_interval_days)


def _p1410_due_date(state, as_of: date, eligible_today: bool) -> tuple[Optional[date], Optional[str]]:
    if not rules_p14.check_stage2_entry_eligible(state):
        return None, "尚未符合第二階段資格條件（P1407Cx1+P1408C>=5+P1409C>=2），P1410C到期日暫不適用"
    if eligible_today:
        return as_of + timedelta(days=SUBSEQUENT_TRACKING_INTERVAL_DAYS), None
    last = state.last_claim_date("P1410C")
    if last is not None:
        return last + timedelta(days=SUBSEQUENT_TRACKING_INTERVAL_DAYS), None
    # 出處：rules_p14.check_p1410_eligibility() 註解「第1次未強制規定間隔
    # 基準（規格書未逐字明示）」——本站保守不推算，交由人工評估。
    return None, "規格書未明訂P1410C第1次申報間隔基準，無法自動推算到期日，需人工評估"


def _p7001_due_date(state, cfg: EligibilityConfig, as_of: date, eligible_today: bool) -> tuple[Optional[date], Optional[str]]:
    if not state.has_claim("P1407C"):
        return None, None
    if cfg.require_p4301_before_p7 and not state.has_claim("P4301C"):
        return None, None
    if eligible_today:
        return as_of + timedelta(days=rules_p7.P7001_SUBSEQUENT_INTERVAL_DAYS), None
    last = state.last_claim_date("P7001C")
    if last is not None:
        return last + timedelta(days=rules_p7.P7001_SUBSEQUENT_INTERVAL_DAYS), None
    enrollment_dates = [d for d in (state.first_claim_date("P1407C"), state.first_claim_date("P4301C")) if d is not None]
    if not enrollment_dates:
        return None, "查無P1407C/P4301C新收案日期，無法計算P7001C首次間隔起算點"
    base_date = max(enrollment_dates)
    return base_date + timedelta(days=rules_p7.P7001_FIRST_INTERVAL_DAYS), None


def compute_follow_up_plan(
    profile: PatientClinicalProfile,
    complication_report: ComplicationReport,
    decision_record: PhysicianDecisionRecord | None = None,
    config: EligibilityConfig | None = None,
    complication_monitoring_config: ComplicationMonitoringConfig | None = None,
    assume_eligible_codes_claimed_today: bool = True,
) -> FollowUpPlan:
    """使用 `profile.enrollment_state` 與 `profile.eligibility_report`
    （取代呼叫端另外傳入 `current_report` 的做法，因 `profile` 已內含）。
    `next_recommended_visit_date` = 所有到期日中最早者；找不到時退回
    `config.fallback_visit_interval_days` 並記 warnings。"""
    cfg = config or EligibilityConfig()
    cmc = complication_monitoring_config or ComplicationMonitoringConfig()
    state = profile.enrollment_state
    as_of = profile.as_of_date

    def _eligible_today(code: str) -> bool:
        if not assume_eligible_codes_claimed_today or profile.eligibility_report is None:
            return False
        result = profile.eligibility_report.get(code)
        return result is not None and result.eligible

    reasons: list[str] = []
    warnings: list[str] = []
    next_code_due_dates: dict[str, Optional[date]] = {}

    p1408_due = _p1408_due_date(state, cfg, as_of, _eligible_today("P1408C"))
    next_code_due_dates["P1408C"] = p1408_due
    if p1408_due is None and state.has_claim("P1407C"):
        warnings.append("無法推算P1408C下次到期日（查無P1407C首次收案日期）")

    p1410_due, p1410_warn = _p1410_due_date(state, as_of, _eligible_today("P1410C"))
    next_code_due_dates["P1410C"] = p1410_due
    if p1410_warn:
        warnings.append(p1410_warn)

    p7001_due, p7001_warn = _p7001_due_date(state, cfg, as_of, _eligible_today("P7001C"))
    next_code_due_dates["P7001C"] = p7001_due
    if p7001_warn:
        warnings.append(p7001_warn)

    monitoring_items: list[MonitoringItem] = []
    complication_categories = {f.category for f in complication_report.findings}
    for rule in cmc.rules:
        if rule.category in complication_categories:
            due = as_of + timedelta(days=rule.interval_days)
            monitoring_items.append(
                MonitoringItem(
                    item_code=rule.item_code,
                    description=rule.description,
                    interval_days=rule.interval_days,
                    due_date=due,
                    source=f"ComplicationMonitoringRule(category={rule.category})",
                    is_placeholder_interval=rule.is_placeholder_interval,
                )
            )

    candidate_dates: list[tuple[date, str]] = []
    for code, due in next_code_due_dates.items():
        if due is not None:
            candidate_dates.append((due, f"照護碼 {code} 下次到期日 {due}"))
    for item in monitoring_items:
        candidate_dates.append((item.due_date, f"併發症監測項目 {item.description}({item.item_code}) 到期日 {item.due_date}"))

    if candidate_dates:
        next_date, reason = min(candidate_dates, key=lambda t: t[0])
        reasons.append(reason)
        if next_date < as_of:
            # ★ 修正（Codex #28）：候選到期日可能早於 as_of（例如已逾期
            # 超過追蹤間隔的 P1408C claim），先前直接把過去的日期當成
            # 「下次回診建議日」回傳，UI 上會顯示「建議回診日：10天前」
            # 這種矛盾的過去日期。到期日本身（用於 reasons/next_code_
            # due_dates 的說明）維持不變、忠實記錄「已逾期多久」，但
            # `next_recommended_visit_date` 是「建議病人下次應該來的日期」
            # ，語意上不可能早於今天——已逾期時應立即收案，即 as_of 當天。
            reasons.append(f"上述到期日 {next_date} 已早於評估日 {as_of}（已逾期），建議回診日收斂為今日 {as_of}")
            next_date = as_of
    else:
        next_date = as_of + timedelta(days=cmc.fallback_visit_interval_days)
        warnings.append("查無可推算之到期日（照護碼與併發症監測項目皆無到期日），採用預設回診間隔 fallback_visit_interval_days")
        reasons.append(f"依 fallback_visit_interval_days={cmc.fallback_visit_interval_days} 天推算之保守回診日 {next_date}")

    # 若醫師已核可任一 URGENT 優先度建議，工程佔位地將回診日上限收緊為
    # as_of + _URGENT_FOLLOWUP_CAP_DAYS（非規格書條文，需臨床端確認合理值）。
    if decision_record is not None:
        urgent_accepted = [
            rec for rec, _decision in decision_record.accepted_or_modified() if rec.priority == RecommendationPriority.URGENT
        ]
        if urgent_accepted:
            urgent_cap = as_of + timedelta(days=_URGENT_FOLLOWUP_CAP_DAYS)
            if urgent_cap < next_date:
                next_date = urgent_cap
                reasons.append(
                    f"醫師已核可URGENT優先度建議（{', '.join(r.rule_id for r in urgent_accepted)}），"
                    f"回診日收緊為 {urgent_cap}（placeholder：{_URGENT_FOLLOWUP_CAP_DAYS}天上限，需臨床端確認）"
                )

    return FollowUpPlan(
        patient_id=profile.patient_id,
        current_as_of_date=as_of,
        next_recommended_visit_date=next_date,
        next_code_due_dates=next_code_due_dates,
        monitoring_items=monitoring_items,
        reasons=reasons,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# v2 新增（架構文件v2 3.12節）：規格§28「已開立醫令完成度追蹤」——與上面
# `compute_follow_up_plan()`（P4P 到期日「應該做什麼」）是不同概念，見架構
# 文件v2 第2節命名統一總表裁定：本站追蹤「已開立醫令是否完成」。
# ---------------------------------------------------------------------------

PendingOrderStatus = Literal["ORDERED", "COMPLETED", "CANCELLED"]


@dataclass(frozen=True)
class PendingOrder:
    order_id: str
    order_type: str
    ordered_date: date
    status: PendingOrderStatus
    completed_date: Optional[date] = None
    triggering_recommendation_id: Optional[str] = None  # 回溯 GuidelineRecommendation.recommendation_id
    source: str = "HIS_CPOE"


class PendingOrderSource(Protocol):
    """唯讀查詢介面，刻意不提供 predict/create 方法——本站只追蹤 HIS 既有
    醫令完成狀態，不自行產生醫令（鐵律4）。目前無預設實作（見架構文件v2
    第4/5節 open_questions#18：需 HIS/LIS/RIS 介接排定後才能串接）。"""

    def get_pending_orders(self, patient_id: str, as_of: date) -> tuple[PendingOrder, ...]: ...


@dataclass(frozen=True)
class OrderTrackingRule:
    order_type: str
    description: str
    staleness_threshold_days: int
    alert_level_if_stale: AlertLevel
    is_placeholder_threshold: bool
    spec_reference: Optional[str]


@dataclass
class OrderTrackingConfig:
    rules: tuple[OrderTrackingRule, ...] = field(
        default_factory=lambda: (
            OrderTrackingRule(
                "FIBROSCAN",
                "FibroScan檢查",
                30,
                AlertLevel.CLINICAL_ATTENTION,
                True,
                "OpenClaw HIS spec §28 例句提及30天，屬敘事範例非正式規則表，需臨床確認",
            ),
            OrderTrackingRule(
                "ECHO",
                "心臟超音波",
                30,
                AlertLevel.CLINICAL_ATTENTION,
                True,
                "工程沿用FibroScan同一placeholder值，規格書無獨立數字",
            ),
        )
    )


@dataclass(frozen=True)
class StaleOrderItem:
    order: PendingOrder
    days_overdue: int
    rule: OrderTrackingRule
    alert_level: AlertLevel
    summary: str


@dataclass
class OrderTrackingReport:
    patient_id: str
    as_of_date: date
    pending_orders: tuple[PendingOrder, ...]
    stale_orders: list[StaleOrderItem]
    warnings: list[str] = field(default_factory=list)


def track_pending_orders(
    profile: PatientClinicalProfile,
    as_of: date,
    order_source: Optional[PendingOrderSource],
    config: Optional[OrderTrackingConfig] = None,
) -> OrderTrackingReport:
    """`order_source` 未提供時，`report.warnings` 記錄「未串接HIS醫令查詢
    介面」並回傳空清單，不可靜默視為「沒有逾期醫令」（鐵律6）。"""
    cfg = config or OrderTrackingConfig()

    if order_source is None:
        return OrderTrackingReport(
            patient_id=profile.patient_id,
            as_of_date=as_of,
            pending_orders=(),
            stale_orders=[],
            warnings=["未串接 HIS 醫令查詢介面（PendingOrderSource 未提供），無法判斷是否有逾期未完成醫令"],
        )

    pending_orders = order_source.get_pending_orders(profile.patient_id, as_of)
    rules_by_type = {r.order_type: r for r in cfg.rules}

    warnings: list[str] = []
    stale_orders: list[StaleOrderItem] = []
    for order in pending_orders:
        if order.status != "ORDERED":
            continue
        rule = rules_by_type.get(order.order_type)
        if rule is None:
            warnings.append(f"order_type={order.order_type!r} 未登記於 OrderTrackingConfig.rules，無法判斷逾期閾值，已略過")
            continue
        days_since_ordered = (as_of - order.ordered_date).days
        if days_since_ordered > rule.staleness_threshold_days:
            stale_orders.append(
                StaleOrderItem(
                    order=order,
                    days_overdue=days_since_ordered - rule.staleness_threshold_days,
                    rule=rule,
                    alert_level=rule.alert_level_if_stale,
                    summary=(
                        f"{rule.description}（醫令{order.order_id}）已開立 {days_since_ordered} 天仍未完成，"
                        f"超過閾值 {rule.staleness_threshold_days} 天"
                        + ("（工程佔位閾值，需臨床確認）" if rule.is_placeholder_threshold else "")
                    ),
                )
            )

    return OrderTrackingReport(
        patient_id=profile.patient_id,
        as_of_date=as_of,
        pending_orders=pending_orders,
        stale_orders=stale_orders,
        warnings=warnings,
    )
