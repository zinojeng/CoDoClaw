"""
【第7站】醫師決策 — 決策支援紀錄，不是自動決策。

★★★ 鐵律3 ★★★ 本模組只負責：(1) 把第6站產生的建議原樣攤平成一份
「全部待決」的紀錄，(2) 提供 `record_decision()` 讓醫師 UI 逐筆記錄
「採納/修改/婉拒」。本模組刻意**不提供**任何 `auto_approve()` /
`auto_order()` / 逾時自動轉換 / 依風險分級自動批次核准 之類的方法——
任何建議在醫師明確呼叫 `record_decision()` 之前，永遠停留在 PENDING，
沒有任何路徑可以繞過人工決策。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Literal, Optional, Protocol, Sequence, runtime_checkable

from .guideline_recommendation import GuidelineRecommendation, GuidelineRecommendationReport, RecommendationPriority


class PhysicianDecisionStatus(str, Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    MODIFIED = "MODIFIED"
    DECLINED = "DECLINED"


@runtime_checkable
class Reviewable(Protocol):
    """讓 `GuidelineRecommendation` 與 `MedicationRecommendation`（及未來
    任何需要走醫師決策的建議型別）都能流入同一份 `PhysicianDecisionRecord`
    （架構文件v2 3.9節）。"""

    recommendation_id: str
    rule_id: str
    title: str
    priority: RecommendationPriority


@dataclass
class PhysicianDecision:
    recommendation_id: str
    status: PhysicianDecisionStatus = PhysicianDecisionStatus.PENDING
    modified_action_text: Optional[str] = None  # status==MODIFIED 時必填
    decline_reason: Optional[str] = None  # status==DECLINED 時必填
    physician_id: Optional[str] = None
    decided_at: Optional[datetime] = None
    free_text_note: Optional[str] = None
    # v2 新增（非破壞性）：status==DECLINED 時，UI 的
    # [Not applicable]/[Contraindicated]/[Dismiss] 三按鈕分別填入對應值；
    # 一般 GuidelineRecommendation 來源可留 None。
    decline_category: Optional[Literal["not_applicable", "contraindicated", "other"]] = None


class DecisionValidationError(Exception):
    pass


@dataclass
class PhysicianDecisionRecord:
    """鐵律3：決策支援，非自動決策。沒有 auto_approve()/auto_order()/
    timeout 自動轉換／依 risk_tier 自動批次核准 等方法。"""

    patient_id: str
    as_of_date: date
    # v2 型別放寬：GuidelineRecommendation | Reviewable（含 MedicationRecommendation），
    # 既有欄位名/語意不變，僅型別註記放寬以支援 §3.9 擴充。
    presented_recommendations: tuple[Reviewable, ...]
    decisions: dict[str, PhysicianDecision] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for rec in self.presented_recommendations:
            if rec.recommendation_id not in self.decisions:
                self.decisions[rec.recommendation_id] = PhysicianDecision(recommendation_id=rec.recommendation_id)

    def record_decision(self, decision: PhysicianDecision) -> None:
        """記錄醫師對單一建議的決定。`recommendation_id` 須存在於
        `presented_recommendations`；DECLINED 需 `decline_reason`，
        MODIFIED 需 `modified_action_text`，否則 raise
        `DecisionValidationError`。"""
        if decision.recommendation_id not in self.decisions:
            raise DecisionValidationError(
                f"recommendation_id={decision.recommendation_id!r} 不存在於本次待決策清單，無法記錄決策"
            )
        if decision.status == PhysicianDecisionStatus.DECLINED and not decision.decline_reason:
            raise DecisionValidationError("status=DECLINED 時 decline_reason 為必填")
        if decision.status == PhysicianDecisionStatus.MODIFIED and not decision.modified_action_text:
            raise DecisionValidationError("status=MODIFIED 時 modified_action_text 為必填")

        if decision.decided_at is None:
            decision.decided_at = datetime.now()

        self.decisions[decision.recommendation_id] = decision

    def pending_count(self) -> int:
        return sum(1 for d in self.decisions.values() if d.status == PhysicianDecisionStatus.PENDING)

    def is_fully_reviewed(self) -> bool:
        return self.pending_count() == 0

    def accepted_or_modified(self) -> list[tuple[Reviewable, PhysicianDecision]]:
        """第8/9站唯一應該消費的介面：僅回傳醫師已核可（採納或修改）的
        建議，婉拒/待決的建議不會出現在這裡。"""
        result: list[tuple[Reviewable, PhysicianDecision]] = []
        for rec in self.presented_recommendations:
            decision = self.decisions[rec.recommendation_id]
            if decision.status in (PhysicianDecisionStatus.ACCEPTED, PhysicianDecisionStatus.MODIFIED):
                result.append((rec, decision))
        return result


def present_for_decision(
    report: "GuidelineRecommendationReport | Sequence[Reviewable]",
    *,
    patient_id: Optional[str] = None,
    as_of_date: Optional[date] = None,
) -> PhysicianDecisionRecord:
    """第7站進入點：把建議清單攤平成一份全 PENDING 的決策紀錄，交給醫師 UI
    逐筆呼叫 `record_decision()`。

    ★ 簽名放寬（架構文件v2 3.9節）：既有呼叫端傳入
    `GuidelineRecommendationReport`（具 `.patient_id`/`.as_of_date`/
    `.recommendations`）仍 100% 相容、零改動——包含關鍵字呼叫
    `present_for_decision(report=...)`（第一參數刻意保留原名 `report`，
    Codex 審閱發現先前一度改名為 `source`會讓既有關鍵字呼叫端 TypeError，
    此為修正）。`MedicationIntelligenceReport` 同樣具備這三個屬性，可比照
    直接傳入。若呼叫端想合併多來源（例如
    `[*guideline_report.recommendations, *medication_report.recommendations]`）
    則傳入純 `Sequence[Reviewable]`，此時 `patient_id`/`as_of_date` 為必填
    keyword 參數（無法從純清單推得）。"""
    if hasattr(report, "recommendations") and hasattr(report, "patient_id") and hasattr(report, "as_of_date"):
        recommendations = tuple(report.recommendations)  # type: ignore[union-attr]
        resolved_patient_id = report.patient_id  # type: ignore[union-attr]
        resolved_as_of_date = report.as_of_date  # type: ignore[union-attr]
    else:
        recommendations = tuple(report)  # type: ignore[arg-type]
        if patient_id is None or as_of_date is None:
            raise DecisionValidationError(
                "present_for_decision() 傳入純 recommendations 清單時，patient_id/as_of_date 為必填 keyword 參數"
            )
        resolved_patient_id = patient_id
        resolved_as_of_date = as_of_date

    return PhysicianDecisionRecord(
        patient_id=resolved_patient_id,
        as_of_date=resolved_as_of_date,
        presented_recommendations=recommendations,
    )


def to_audit_trail(record: PhysicianDecisionRecord) -> list[dict]:
    """目前只是單一快照（每個 recommendation_id 只保留最後一次決定），
    非逐次異動歷程。若日後法規要求逐次異動歷程，需另外擴充（見架構文件
    4.3節）。"""
    trail: list[dict] = []
    for rec in record.presented_recommendations:
        decision = record.decisions[rec.recommendation_id]
        trail.append(
            {
                "recommendation_id": rec.recommendation_id,
                "rule_id": rec.rule_id,
                "title": rec.title,
                "status": decision.status.value,
                "modified_action_text": decision.modified_action_text,
                "decline_reason": decision.decline_reason,
                "physician_id": decision.physician_id,
                "decided_at": decision.decided_at.isoformat() if decision.decided_at else None,
                "free_text_note": decision.free_text_note,
            }
        )
    return trail
