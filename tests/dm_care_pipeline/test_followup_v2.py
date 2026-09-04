"""
`followup.py` v2 擴充測試：規格§28「已開立醫令完成度追蹤」
（`PendingOrder`/`PendingOrderSource`/`OrderTrackingConfig`/
`track_pending_orders()`）。既有 P4P 到期日邏輯（`compute_follow_up_plan()`）
見 `tests/test_care_pipeline.py`，此檔案不重複覆蓋。
"""

from __future__ import annotations

from datetime import date, timedelta

from dm_eligibility.models import PatientEnrollmentState

from dm_care_pipeline.alert import AlertLevel
from dm_care_pipeline.data_integration import build_patient_clinical_profile
from dm_care_pipeline.followup import (
    OrderTrackingConfig,
    OrderTrackingRule,
    PendingOrder,
    track_pending_orders,
)

AS_OF = date(2024, 6, 1)


class _FakeOrderSource:
    def __init__(self, orders: tuple[PendingOrder, ...]):
        self._orders = orders

    def get_pending_orders(self, patient_id: str, as_of: date) -> tuple[PendingOrder, ...]:
        return self._orders


def _profile():
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, encounters=[])
    return build_patient_clinical_profile(state)


# ---------------------------------------------------------------------------
# track_pending_orders()
# ---------------------------------------------------------------------------


def test_no_order_source_warns_and_returns_empty_not_silently_ok():
    report = track_pending_orders(_profile(), AS_OF, order_source=None)
    assert report.pending_orders == ()
    assert report.stale_orders == []
    assert any("未串接" in w for w in report.warnings)


def test_order_within_threshold_is_not_stale():
    order = PendingOrder(order_id="O1", order_type="FIBROSCAN", ordered_date=AS_OF - timedelta(days=10), status="ORDERED")
    report = track_pending_orders(_profile(), AS_OF, order_source=_FakeOrderSource((order,)))
    assert report.stale_orders == []
    assert report.pending_orders == (order,)


def test_order_beyond_threshold_is_stale_with_correct_days_overdue():
    order = PendingOrder(order_id="O1", order_type="FIBROSCAN", ordered_date=AS_OF - timedelta(days=40), status="ORDERED")
    report = track_pending_orders(_profile(), AS_OF, order_source=_FakeOrderSource((order,)))
    assert len(report.stale_orders) == 1
    item = report.stale_orders[0]
    assert item.days_overdue == 10  # 40天已開立 - 30天閾值
    assert item.alert_level == AlertLevel.CLINICAL_ATTENTION


def test_completed_order_never_flagged_stale():
    order = PendingOrder(
        order_id="O1",
        order_type="FIBROSCAN",
        ordered_date=AS_OF - timedelta(days=100),
        status="COMPLETED",
        completed_date=AS_OF - timedelta(days=5),
    )
    report = track_pending_orders(_profile(), AS_OF, order_source=_FakeOrderSource((order,)))
    assert report.stale_orders == []


def test_cancelled_order_never_flagged_stale():
    order = PendingOrder(order_id="O1", order_type="ECHO", ordered_date=AS_OF - timedelta(days=100), status="CANCELLED")
    report = track_pending_orders(_profile(), AS_OF, order_source=_FakeOrderSource((order,)))
    assert report.stale_orders == []


def test_unregistered_order_type_warns_and_is_skipped():
    order = PendingOrder(order_id="O1", order_type="MRI", ordered_date=AS_OF - timedelta(days=100), status="ORDERED")
    report = track_pending_orders(_profile(), AS_OF, order_source=_FakeOrderSource((order,)))
    assert report.stale_orders == []
    assert any("MRI" in w for w in report.warnings)


def test_custom_config_overrides_default_rules():
    order = PendingOrder(order_id="O1", order_type="FIBROSCAN", ordered_date=AS_OF - timedelta(days=5), status="ORDERED")
    cfg = OrderTrackingConfig(
        rules=(OrderTrackingRule("FIBROSCAN", "FibroScan檢查", 3, AlertLevel.SAFETY_ALERT, False, "自訂測試閾值"),)
    )
    report = track_pending_orders(_profile(), AS_OF, order_source=_FakeOrderSource((order,)), config=cfg)
    assert len(report.stale_orders) == 1
    assert report.stale_orders[0].alert_level == AlertLevel.SAFETY_ALERT


def test_default_config_has_fibroscan_and_echo_rules():
    cfg = OrderTrackingConfig()
    order_types = {r.order_type for r in cfg.rules}
    assert order_types == {"FIBROSCAN", "ECHO"}
    assert all(r.is_placeholder_threshold for r in cfg.rules)
