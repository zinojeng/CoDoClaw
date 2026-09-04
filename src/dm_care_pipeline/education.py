"""
【第8站】病人衛教 — 「衛教主題代碼 → 衛教資源」可設定對照表架構。

★★★ 鐵律4 ★★★ 本模組沒有現成的衛教文案來源，`EducationTopicMappingConfig`
的預設內容只放 2-3 筆**明確標示 placeholder** 的範例（`is_placeholder=True`,
`review_status="UNVERIFIED"`），不生成大段「醫療衛教文字」冒充正式衛教
教材。正式上線前需衛教/個管團隊提供正式教材，並把對應資源的
`review_status` 改為 `"CLINICAL_REVIEWED"`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import TYPE_CHECKING, Optional, Sequence

from .clinical_data_object import ClinicalDomain, ClinicalStatus
from .complication_identification import ComplicationReport
from .physician_decision import PhysicianDecisionRecord, PhysicianDecisionStatus
from .trend_analysis import ClinicalTrendReport, TrendDirection

if TYPE_CHECKING:  # pragma: no cover - 型別提示用，避免執行期循環 import
    from .clinical_state import PatientClinicalState
    from .followup import PendingOrder


@dataclass(frozen=True)
class EducationResource:
    title: str
    content_ref: str  # 例如 "placeholder://foot_care_v0"
    is_placeholder: bool = True
    review_status: str = "UNVERIFIED"  # "UNVERIFIED" | "CLINICAL_REVIEWED"


def _default_complication_to_topics() -> dict[str, tuple[str, ...]]:
    # TODO：僅示範 2 筆對照，其餘 COMPLICATION_ICD10_PREFIXES 類別
    # （RETINOPATHY/NEUROPATHY/CVD/CEREBROVASCULAR）尚未對應主題，需衛教
    # 團隊補齊。
    return {
        "NEPHROPATHY": ("RENAL_DIET_BASIC",),
        "PVD": ("FOOT_CARE_BASIC",),
    }


def _default_resources_by_topic() -> dict[str, tuple[EducationResource, ...]]:
    # 鐵律4：以下 3 筆皆為明確標示 placeholder 的範例，不是正式衛教教材。
    return {
        "FOOT_CARE_BASIC": (
            EducationResource(title="足部照護衛教（placeholder）", content_ref="placeholder://foot_care_v0"),
        ),
        "RENAL_DIET_BASIC": (
            EducationResource(title="腎臟保健飲食衛教（placeholder）", content_ref="placeholder://renal_diet_v0"),
        ),
        "GLYCEMIC_CONTROL_BASIC": (
            EducationResource(title="血糖控制衛教（placeholder）", content_ref="placeholder://glycemic_control_v0"),
        ),
        # EYE_EXAM_BASIC 刻意留空：示範「主題代碼存在，但查無對應衛教資源」
        # 的情境（select_education_topics 應記 warnings、
        # needs_manual_review=True，而非靜默略過）。
    }


@dataclass
class EducationTopicMappingConfig:
    """鐵律4：只放 2-3 筆明確標示 placeholder 的範例，不生成正式衛教教材。"""

    complication_to_topics: dict[str, tuple[str, ...]] = field(default_factory=_default_complication_to_topics)
    resources_by_topic: dict[str, tuple[EducationResource, ...]] = field(default_factory=_default_resources_by_topic)


@dataclass(frozen=True)
class EducationTopicSelection:
    topic_code: str
    trigger_reason: str
    resources: tuple[EducationResource, ...]


@dataclass
class EducationPlan:
    patient_id: str
    as_of_date: date
    topics: list[EducationTopicSelection]
    needs_manual_review: bool
    warnings: list[str] = field(default_factory=list)


def select_education_topics(
    decision_record: PhysicianDecisionRecord,
    complication_report: ComplicationReport,
    config: EducationTopicMappingConfig | None = None,
) -> EducationPlan:
    """直接消費 `decision_record.accepted_or_modified()`（醫師實際核可的
    建議，婉拒的不觸發衛教）與 `complication_report.findings`，取
    `recommendation.education_topic_code` 與
    `complication_to_topics[finding.category]` 兩個來源聯集、去重、查
    `resources_by_topic`；查無資源時不中斷，記 warnings 並設
    `needs_manual_review=True`。"""
    cfg = config or EducationTopicMappingConfig()

    topic_reasons: dict[str, list[str]] = {}

    for rec, decision in decision_record.accepted_or_modified():
        # v2 起 decision_record.accepted_or_modified() 型別放寬為
        # Reviewable（physician_decision.py §3.9 擴充），可能混入不具
        # education_topic_code 屬性的 MedicationRecommendation；用
        # getattr 安全略過，不 AttributeError（既有 GuidelineRecommendation
        # 行為完全不變）。
        education_topic_code = getattr(rec, "education_topic_code", None)
        if education_topic_code:
            topic_reasons.setdefault(education_topic_code, []).append(
                f"醫師已{decision.status.value}建議「{rec.title}」（rule_id={rec.rule_id}）"
            )

    for finding in complication_report.findings:
        for topic_code in cfg.complication_to_topics.get(finding.category, ()):
            topic_reasons.setdefault(topic_code, []).append(
                f"併發症辨識命中{finding.category}（ICD-10碼={finding.matched_icd10_codes}）"
            )

    topics: list[EducationTopicSelection] = []
    warnings: list[str] = []
    needs_manual_review = False

    for topic_code in sorted(topic_reasons.keys()):
        reasons = topic_reasons[topic_code]
        resources = cfg.resources_by_topic.get(topic_code)
        if not resources:
            warnings.append(f"主題代碼 {topic_code} 查無對應衛教資源，需人工提供")
            needs_manual_review = True
            resources = ()
        elif any(r.is_placeholder for r in resources):
            needs_manual_review = True

        topics.append(EducationTopicSelection(topic_code=topic_code, trigger_reason="；".join(reasons), resources=tuple(resources)))

    return EducationPlan(
        patient_id=decision_record.patient_id,
        as_of_date=decision_record.as_of_date,
        topics=topics,
        needs_manual_review=needs_manual_review,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# v2 新增（架構文件v2 3.11節）：規格§27 結構化病人衛教報告。
#
# ★★★ 鐵律4 落地 ★★★：`EducationSectionTemplateRule.
# requires_numeric_disclosure=False` 的模板禁止填入任何自行估算數字；
# 觸發 finding 若 `is_placeholder=True`（例如 Tier B 來源），本站強制只
# 考慮 `requires_numeric_disclosure=False` 的模板，不得把未驗證數字揭露給
# 病人。
# ---------------------------------------------------------------------------


class EducationSectionCode(str, Enum):
    GLYCEMIC = "GLYCEMIC"
    RENAL = "RENAL"
    CARDIAC = "CARDIAC"
    HEPATIC = "HEPATIC"
    FOOT = "FOOT"
    EYE = "EYE"


# EducationSectionCode → ClinicalDomain 對照（本檔案新增，規格pseudocode
# 未列出）。★ 修正（Codex 審閱發現）：`generate_patient_education_report()`
# 原本只比對 finding.status 是否命中 rule.trigger_status，未檢查
# finding.domain 是否真的對應該 section——一個 FOOT/LIVER/HYPOGLYCEMIA 領域
# 的 HIGH_RISK finding 會被誤套用 CARDIAC 心衰竭衛教文案，對病人產生無關
# 甚至誤導的說明。本表補上 domain 篩選層。
EDUCATION_SECTION_DOMAIN: dict[EducationSectionCode, ClinicalDomain] = {
    EducationSectionCode.GLYCEMIC: ClinicalDomain.GLYCEMIC_CONTROL,
    EducationSectionCode.RENAL: ClinicalDomain.KIDNEY,
    EducationSectionCode.CARDIAC: ClinicalDomain.HEART_FAILURE,
    EducationSectionCode.HEPATIC: ClinicalDomain.LIVER,
    EducationSectionCode.FOOT: ClinicalDomain.FOOT,
    EducationSectionCode.EYE: ClinicalDomain.EYE,
}


@dataclass(frozen=True)
class EducationSectionTemplateRule:
    section_code: EducationSectionCode
    trigger_status: tuple[ClinicalStatus, ...]
    template: str
    requires_numeric_disclosure: bool  # False=Tier B或需保護病人閱讀負擔者，模板禁止填入任何自行估算數字
    is_placeholder: bool = True
    review_status: str = "UNVERIFIED"


def _default_section_rules() -> tuple[EducationSectionTemplateRule, ...]:
    # 鐵律4：以下 2 筆皆為明確標示 placeholder 的範例，正式上線前需衛教/
    # 個管團隊提供正式教材文案。
    return (
        EducationSectionTemplateRule(
            EducationSectionCode.GLYCEMIC,
            (ClinicalStatus.HIGH_RISK, ClinicalStatus.CARE_GAP),
            "您的HbA1c最近從 {v1}% → {v2}% → {v3}%，{trend_phrase}。",
            requires_numeric_disclosure=True,
        ),
        EducationSectionTemplateRule(
            EducationSectionCode.CARDIAC,
            (ClinicalStatus.HIGH_RISK,),
            "目前沒有確定心臟衰竭，但因為糖尿病及其他條件，屬於較需要注意的族群，因此醫師可能會安排進一步心臟檢查。",
            requires_numeric_disclosure=False,
        ),
    )


@dataclass
class EducationReportBuilderConfig:
    section_rules: tuple[EducationSectionTemplateRule, ...] = field(default_factory=_default_section_rules)


@dataclass(frozen=True)
class EducationReportSection:
    section_code: EducationSectionCode
    title: str
    body_text: str
    source_finding_ids: tuple[str, ...]
    is_placeholder: bool
    review_status: str


@dataclass
class PatientEducationReport:
    patient_id: str
    as_of_date: date
    sections: list[EducationReportSection]
    today_actions: list[str]  # 來源=decision_record.accepted_or_modified() 逐條 + PendingOrder
    resource_topics: list[EducationTopicSelection]  # 重用既有 select_education_topics() 輸出
    needs_manual_review: bool
    warnings: list[str] = field(default_factory=list)


_HBA1C_TREND_PHRASE: dict[TrendDirection, str] = {
    TrendDirection.RISING: "呈現上升趨勢，建議與醫師討論加強血糖控制",
    TrendDirection.FALLING: "呈現下降趨勢",
    TrendDirection.STABLE: "呈現穩定趨勢",
    TrendDirection.INSUFFICIENT_DATA: "資料不足，無法判斷趨勢",
}


def _render_glycemic_hba1c_template(rule: EducationSectionTemplateRule, trend_report: ClinicalTrendReport) -> Optional[str]:
    """★ 只有真正存在 >=3 筆 HbA1c 歷史數值時才套用本模板（規格§27範例
    逐字給 v1/v2/v3 三個數字）；不足 3 筆時本函式回傳 None，不以「?」或
    其他佔位符號冒充真實數字（鐵律4更嚴格落地：寧可不顯示這個 section，
    也不顯示不完整/臆造的數字）。"""
    marker = next((m for m in trend_report.marker_trends if m.marker_name == "HBA1C"), None)
    if marker is None or len(marker.data_points) < 3:
        return None
    points = sorted(marker.data_points, key=lambda p: p[0])[-3:]
    v1, v2, v3 = (f"{value:.1f}" for _, value in points)
    trend_phrase = _HBA1C_TREND_PHRASE.get(marker.direction, "趨勢不明")
    return rule.template.format(v1=v1, v2=v2, v3=v3, trend_phrase=trend_phrase)


def generate_patient_education_report(
    clinical_state: "PatientClinicalState",
    trend_report: ClinicalTrendReport,
    complication_report: ComplicationReport,
    decision_record: PhysicianDecisionRecord,
    pending_orders: Sequence["PendingOrder"] = (),
    config: Optional[EducationReportBuilderConfig] = None,
    topic_mapping_config: Optional[EducationTopicMappingConfig] = None,
) -> PatientEducationReport:
    """依 `clinical_state.findings` 的 `status` 逐一比對 `section_rules`
    套版；Tier B 來源 finding（`is_placeholder=True`）強制走
    `requires_numeric_disclosure=False` 模板，任何情況下都不得自行計算/
    編造具體風險數字。`resource_topics` 重用既有
    `select_education_topics()`（不重算/不重複實作，鐵律7）。

    ★ 修正（Codex 審閱發現）：新增 `topic_mapping_config` 參數（keyword-only
    語意，預設 None）——先前本函式內部呼叫 `select_education_topics()` 時
    永遠不傳自訂 `EducationTopicMappingConfig`，即使呼叫端（`pipeline.
    finalize_pipeline()`）對外把 `education_config` 正確套用在
    `education_plan.topics` 上，`education_report.resource_topics` 仍會用
    預設設定重算一次，兩者結果會不一致。"""
    cfg = config or EducationReportBuilderConfig()
    sections: list[EducationReportSection] = []
    warnings: list[str] = []
    needs_manual_review = False

    for finding in clinical_state.findings:
        for rule in cfg.section_rules:
            if finding.status not in rule.trigger_status:
                continue
            # ★ 修正（Codex 審閱發現）：原本只比對 status，未檢查
            # finding.domain 是否對應該 section——會把 FOOT/LIVER 等領域的
            # HIGH_RISK finding 誤套用 CARDIAC 心衰竭衛教文案。
            expected_domain = EDUCATION_SECTION_DOMAIN.get(rule.section_code)
            if expected_domain is not None and finding.domain != expected_domain:
                continue
            if finding.is_placeholder and rule.requires_numeric_disclosure:
                # ★ 鐵律4：Tier B/is_placeholder 來源不得套用需要具體數字的模板。
                continue

            if rule.requires_numeric_disclosure and rule.section_code == EducationSectionCode.GLYCEMIC:
                body_text = _render_glycemic_hba1c_template(rule, trend_report)
                if body_text is None:
                    warnings.append(
                        f"finding_id={finding.finding_id} 命中 {rule.section_code.value} 數字揭露模板，"
                        "但 HbA1c 歷史資料點不足3筆，已略過此 section（不臆造缺漏數字）"
                    )
                    continue
            else:
                body_text = rule.template

            if rule.is_placeholder:
                needs_manual_review = True

            sections.append(
                EducationReportSection(
                    section_code=rule.section_code,
                    title=f"{rule.section_code.value} 衛教說明",
                    body_text=body_text,
                    source_finding_ids=(finding.finding_id,),
                    is_placeholder=rule.is_placeholder,
                    review_status=rule.review_status,
                )
            )

    today_actions: list[str] = []
    for rec, decision in decision_record.accepted_or_modified():
        # ★ 修正（Codex #27）：status==MODIFIED 時，醫師實際決定的內容是
        # `decision.modified_action_text`（必填欄位，見 record_decision()
        # 驗證），不是原始建議的 `rec.title`——先前不論 ACCEPTED 或
        # MODIFIED 一律顯示 rec.title，等於病人衛教內容會顯示醫師「已修改
        # 但從未真正採用」的原始建議，而非醫師實際核可的內容。
        if decision.status == PhysicianDecisionStatus.MODIFIED and decision.modified_action_text:
            today_actions.append(f"醫師已{decision.status.value}：{decision.modified_action_text}")
        else:
            today_actions.append(f"醫師已{decision.status.value}：{rec.title}")
    for order in pending_orders:
        # ★ 修正（Codex 審閱發現）：原本「非COMPLETED即視為待完成」會把
        # CANCELLED（已取消）也顯示成「待完成醫令」，誤導病人以為還有事要做。
        # 只有真正 status=="ORDERED" 才算待完成（followup.PendingOrderStatus
        # 三值：ORDERED/COMPLETED/CANCELLED）。
        status = getattr(order, "status", None)
        if status == "ORDERED":
            today_actions.append(f"待完成醫令：{getattr(order, 'order_type', '未知項目')}")

    resource_plan = select_education_topics(decision_record, complication_report, topic_mapping_config)
    if resource_plan.needs_manual_review:
        needs_manual_review = True
    warnings.extend(resource_plan.warnings)

    return PatientEducationReport(
        patient_id=clinical_state.patient_id,
        as_of_date=clinical_state.as_of_date,
        sections=sections,
        today_actions=today_actions,
        resource_topics=resource_plan.topics,
        needs_manual_review=needs_manual_review,
        warnings=warnings,
    )
