"""
【Pre-Visit Diabetes Brief】規格§21「六步驟流程」+ §22-26「Widget 1-6」+
§32 Alert 分級的組裝層——「跑完全部站點後的純格式化/組裝函式」。

★★★ 鐵律7 落地 ★★★：本檔案**不重新計算任何邏輯**。`complication_map`
直接取 `clinical_state.domain_summaries`（不重新計算紅黃綠三色——這正是
`clinical_state.derive_clinical_state()` 已完成的職責）；`evidence_index`
直接以 `clinical_state.findings` 建表；`alert_report` 呼叫
`alert.classify_alert_batch(clinical_state.findings)`（全管線唯一 Alert
分級權威點）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Mapping, Optional

from .alert import AlertReport, classify_alert_batch
from .clinical_data_object import ClinicalDomain, ClinicalFinding
from .clinical_state import TrafficLight
from .guideline_recommendation import GuidelineRecommendation
from .trend_analysis import MarkerTrend, QualityMetricTier, TrendDirection

if TYPE_CHECKING:  # pragma: no cover - 型別提示用，避免執行期循環 import
    from .alert import AlertClassificationConfig
    from .calculators.base import CalculatorResult
    from .clinical_state import PatientClinicalState
    from .guideline_recommendation import GuidelineRecommendationReport
    from .physician_decision import PhysicianDecision, PhysicianDecisionRecord
    from .pipeline_models import DataGapFlag, PatientClinicalProfile
    from .trend_analysis import ClinicalTrendReport


@dataclass(frozen=True)
class TodayMetric:
    marker_name: str
    latest_value: float | None
    latest_date: date | None
    direction: TrendDirection
    control_tier: QualityMetricTier


@dataclass(frozen=True)
class ComplicationMapEntry:
    domain: ClinicalDomain
    traffic_light: TrafficLight
    summary_text: str
    finding_ids: tuple[str, ...]


@dataclass
class PreVisitDiabetesBrief:
    patient_id: str
    as_of_date: date
    generated_at: datetime
    today_widget: dict[str, TodayMetric]  # §22 Widget1
    trend_widget: tuple[MarkerTrend, ...]  # §22 Widget2（直接reference trend_report.marker_trends）
    complication_map: list[ComplicationMapEntry]  # §23（來源=clinical_state.domain_summaries）
    advanced_risk_widget: tuple["CalculatorResult", ...]  # §24（原樣帶出，Tier B顯示model_provenance而非數字）
    guideline_gap_widget: tuple[tuple[GuidelineRecommendation, "PhysicianDecision"], ...]  # §25
    evidence_index: dict[str, ClinicalFinding]  # §26 Why?，key=finding_id
    alert_report: AlertReport  # §32
    data_gaps: tuple["DataGapFlag", ...]


def _build_today_widget(trend_report: "ClinicalTrendReport") -> dict[str, TodayMetric]:
    return {
        mt.marker_name: TodayMetric(
            marker_name=mt.marker_name,
            latest_value=mt.latest_value,
            latest_date=mt.latest_result_date,
            direction=mt.direction,
            control_tier=mt.control_tier,
        )
        for mt in trend_report.marker_trends
    }


def _build_complication_map(clinical_state: "PatientClinicalState") -> list[ComplicationMapEntry]:
    return [
        ComplicationMapEntry(
            domain=summary.domain,
            traffic_light=summary.traffic_light,
            summary_text=summary.headline,
            finding_ids=summary.finding_ids,
        )
        for summary in clinical_state.domain_summaries.values()
    ]


def _build_guideline_gap_widget(
    decision_record: "PhysicianDecisionRecord",
) -> tuple[tuple[GuidelineRecommendation, "PhysicianDecision"], ...]:
    """`decision_record.presented_recommendations` 自 v2 起型別放寬為
    `Reviewable`（可能混入 `MedicationRecommendation`，見
    `physician_decision.py` §3.9 擴充），本 widget 型別依規格為
    `tuple[GuidelineRecommendation, PhysicianDecision]`，故僅挑出真正的
    `GuidelineRecommendation` 實例——`MedicationRecommendation` 走
    `medication_intelligence.py` 自己的 review panel，不混入本 widget。"""
    pairs: list[tuple[GuidelineRecommendation, "PhysicianDecision"]] = []
    for rec in decision_record.presented_recommendations:
        if isinstance(rec, GuidelineRecommendation):
            pairs.append((rec, decision_record.decisions[rec.recommendation_id]))
    return tuple(pairs)


def generate_pre_visit_brief(
    profile: "PatientClinicalProfile",
    trend_report: "ClinicalTrendReport",
    clinical_state: "PatientClinicalState",
    calculator_results: Mapping[str, "CalculatorResult"],
    guideline_report: "GuidelineRecommendationReport",
    decision_record: "PhysicianDecisionRecord",
    alert_config: Optional["AlertClassificationConfig"] = None,
) -> PreVisitDiabetesBrief:
    """純組裝函式：`complication_map` 直接取
    `clinical_state.domain_summaries`（不重新計算紅黃綠三色）；
    `evidence_index` 直接以 `clinical_state.findings` 建表；
    `alert_report = alert.classify_alert_batch(clinical_state.findings)`。
    `guideline_report` 參數本身目前僅用於型別完整性（規格pseudocode 明列
    於簽名），實際 `guideline_gap_widget` 內容取自
    `decision_record.presented_recommendations`（已包含
    `guideline_report.recommendations` 攤平後的全部項目，鐵律7不重複讀
    兩次同一份資料）。

    ★ `alert_config` 為本檔案新增的 keyword-only 選填參數（規格pseudocode
    未列出；Codex 審閱發現 `pipeline.run_stages_1_to_7()` 原本接受
    `alert_config` 卻從未傳到任何地方，是個沒有作用的參數，此為補上實際
    串接）。未提供時沿用 `alert.classify_alert_batch()` 的預設
    `AlertClassificationConfig()`。"""
    evidence_index = {f.finding_id: f for f in clinical_state.findings}
    alert_report = classify_alert_batch(list(clinical_state.findings), config=alert_config)

    return PreVisitDiabetesBrief(
        patient_id=profile.patient_id,
        as_of_date=profile.as_of_date,
        generated_at=datetime.now(),
        today_widget=_build_today_widget(trend_report),
        trend_widget=tuple(trend_report.marker_trends),
        complication_map=_build_complication_map(clinical_state),
        advanced_risk_widget=tuple(calculator_results.values()),
        guideline_gap_widget=_build_guideline_gap_widget(decision_record),
        evidence_index=evidence_index,
        alert_report=alert_report,
        data_gaps=tuple(clinical_state.data_gaps),
    )
