"""
【Alert 分級】規格§32 三級 Alert 分級的唯一權威計算點。

★★★ 鐵律：避免「多處各自判斷同一件事」 ★★★——本站是全管線唯一應該計算
`AlertLevel` 的地方。`guideline_recommendation.RecommendationRule.alert_level`
只是規則定義端的建議性 hint（供 UI 預覽/排序參考），**不是**最終分級；
最終分級一律由本檔案的 `classify_alert()`/`classify_alert_batch()` 對
`ClinicalFinding` 計算（架構文件v2 第2節命名統一總表裁定）。

★★★ 落地精神（規格§32「避免alert fatigue」）★★★：`AlertReport.
safety_alert_count` 供呼叫端決定「僅 >0 才彈窗」，其餘等級不應打斷醫師
工作流程。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional, Sequence

from .clinical_data_object import ClinicalFinding, ClinicalStatus


class AlertLevel(str, Enum):
    INFORMATION = "information"
    CLINICAL_ATTENTION = "clinical_attention"
    SAFETY_ALERT = "safety_alert"


@dataclass
class AlertClassificationConfig:
    # 規格§32逐字5例，非窮舉表（`is_exhaustive=False`），需臨床/藥劑補齊
    # （目前全管線尚未有任何一站會產生這 5 個 category 字串本身——
    # `MEDICATION_CONTRAINDICATION`/`MAJOR_DRUG_INTERACTION` 等需未來
    # Medication Agent 真正串接禁忌症/交互作用判斷後才可能觸發，見架構文件
    # v2 3.8節 `NullContraindicationChecker` 的 `is_exhaustive` 精神呼應）。
    safety_alert_categories: frozenset[str] = frozenset(
        {
            "MEDICATION_CONTRAINDICATION",
            "SEVERE_HYPOGLYCEMIA_RISK",
            "DANGEROUS_LAB_RESULT",
            "MAJOR_DRUG_INTERACTION",
            "ORDER_CONFLICT",
        }
    )
    is_exhaustive: bool = False
    clinical_attention_status: frozenset[ClinicalStatus] = frozenset(
        {ClinicalStatus.HIGH_RISK, ClinicalStatus.CARE_GAP}
    )


def classify_alert(
    finding: ClinicalFinding,
    is_safety_critical_override: bool = False,
    category: Optional[str] = None,
    config: Optional[AlertClassificationConfig] = None,
) -> AlertLevel:
    """優先序：
    (1) `is_safety_critical_override` 或 `category` 命中
        `config.safety_alert_categories` → `SAFETY_ALERT`；
    (2) `finding.status` 落在 `config.clinical_attention_status` →
        `CLINICAL_ATTENTION`；
    (3) 其餘 → `INFORMATION`。

    `is_safety_critical_override` 是留給未來 Medication Intelligence Agent
    藥物交互作用判斷的掛勾點，v2 範圍內永遠不會被觸發（該邏輯尚未建置，
    呼叫端一律傳 False/不傳）。`category` 是本檔案新增的參數（規格pseudocode
    未列出，但 `safety_alert_categories` 本身是一組字串 category，
    `ClinicalFinding` 沒有對應欄位，需呼叫端明確傳入該 finding 屬於哪個
    safety category 才能比對；未提供時只走 override/status 兩層判斷）。
    """
    cfg = config or AlertClassificationConfig()

    if is_safety_critical_override or (category is not None and category in cfg.safety_alert_categories):
        return AlertLevel.SAFETY_ALERT
    if finding.status in cfg.clinical_attention_status:
        return AlertLevel.CLINICAL_ATTENTION
    return AlertLevel.INFORMATION


@dataclass
class AlertReport:
    patient_id: str
    as_of_date: date
    by_level: dict[AlertLevel, list[ClinicalFinding]]
    safety_alert_count: int  # 僅 >0 才彈窗，避免 alert fatigue（§32原文精神）


def classify_alert_batch(
    findings: Sequence[ClinicalFinding],
    categories: Optional[dict[str, str]] = None,  # finding_id -> safety category（選填，見 classify_alert() 說明）
    config: Optional[AlertClassificationConfig] = None,
    *,
    patient_id: Optional[str] = None,
    as_of_date: Optional[date] = None,
) -> AlertReport:
    """規格pseudocode 簽名只有 `(findings, config=None)`，未帶
    `patient_id`/`as_of_date`——`AlertReport` 需要的這兩個欄位若未由呼叫端
    明確提供，退回從 `findings` 推斷（取第一筆的 `patient_id`/`date`）。
    `findings` 為空且未提供 `patient_id`/`as_of_date` 時無從推斷，
    `patient_id=""`、`as_of_date=None`（本檔案刻意不虛構一個日期）；
    呼叫端應自行確保空清單是預期情境（例如病人完全無 finding）。

    ★ 修正（Codex #30）：先前唯一的來源是「推斷自 findings[0]」——但
    finding 的 `date` 是**證據日期**（例如檢驗抽血日、診斷紀錄日），可能
    是好幾年前，不是「本次評估」的日期；`findings` 為空時更直接回傳
    `patient_id=""`/`as_of_date=None`，即使呼叫端（例如
    `pre_visit_brief.generate_pre_visit_brief()`）明明知道真正的
    `profile.patient_id`/`profile.as_of_date`。新增 keyword-only
    `patient_id`/`as_of_date` 參數，提供時優先採用（呼叫端明確知道的
    評估基準日，優於從證據日期反推）；未提供時維持原本推斷邏輯，向下
    相容規格pseudocode 的最簡呼叫方式
    `classify_alert_batch(findings)`。

    `categories` 為本檔案新增的選填參數（見 `classify_alert()` 說明），
    未提供時所有 finding 只走 override/status 兩層判斷，向下相容
    `classify_alert_batch(findings)` 這種最簡呼叫方式。"""
    cfg = config or AlertClassificationConfig()
    categories = categories or {}

    by_level: dict[AlertLevel, list[ClinicalFinding]] = {level: [] for level in AlertLevel}
    for finding in findings:
        level = classify_alert(finding, category=categories.get(finding.finding_id), config=cfg)
        by_level[level].append(finding)

    resolved_patient_id = patient_id if patient_id is not None else (findings[0].patient_id if findings else "")
    resolved_as_of_date = as_of_date if as_of_date is not None else (findings[0].date if findings else None)

    return AlertReport(
        patient_id=resolved_patient_id,
        as_of_date=resolved_as_of_date,  # type: ignore[arg-type]
        by_level=by_level,
        safety_alert_count=len(by_level[AlertLevel.SAFETY_ALERT]),
    )
