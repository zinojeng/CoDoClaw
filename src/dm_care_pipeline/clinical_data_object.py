"""
【v2 核心型別】規格§30「OpenClaw Clinical Data Object」與§31「Calculator
Result Object」的共用型別定義。全管線唯一權威來源——`clinical_state.py`、
`calculators/base.py`、`guideline_recommendation.py`、`alert.py`、
`education.py` 等站點一律 import 本檔案的型別，不得自建同義型別（見
docs/臨床決策支援管線設計_v2_OpenClaw.md 第2節「命名統一總表」裁定）。

★★★ 鐵律3 ★★★：所有計算結果/併發症判斷/guideline建議，一律採用本檔案
`ClinicalFinding` 的欄位結構，且 `status` 欄位只能是 `ClinicalStatus` 定義
的四值之一（confirmed/suspected/high_risk/care_gap），不可以只有布林值。

本檔案刻意不 import `clinical_state.py`／`guideline_recommendation.py`（避免
循環 import：`clinical_state.py` 需要 import 本檔案的 `ClinicalFinding` 等
型別）。`compose_clinical_data_objects()` 對這兩個模組的型別只使用
`TYPE_CHECKING` guard 下的字串前向參照，執行期以鴨子定型（duck typing）存取
其屬性。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover - 型別提示用，避免執行期循環 import
    from .clinical_state import PatientClinicalState
    from .guideline_recommendation import GuidelineRecommendationReport
    from .physician_decision import PhysicianDecisionRecord


class ClinicalDomain(str, Enum):
    """規格§14 微血管/巨血管/心臟代謝三大群組所涵蓋之臨床領域，全管線唯一
    權威列舉。"""

    GLYCEMIC_CONTROL = "GLYCEMIC_CONTROL"
    KIDNEY = "KIDNEY"
    EYE = "EYE"
    NEUROPATHY = "NEUROPATHY"
    FOOT = "FOOT"
    HEART_FAILURE = "HEART_FAILURE"
    ASCVD = "ASCVD"
    CEREBROVASCULAR = "CEREBROVASCULAR"
    PAD = "PAD"
    LIVER = "LIVER"
    HYPOGLYCEMIA = "HYPOGLYCEMIA"
    BLOOD_PRESSURE = "BLOOD_PRESSURE"
    WEIGHT_OBESITY = "WEIGHT_OBESITY"


# 規格§14 Microvascular/Macrovascular/Cardiometabolic 三大群組的 UI 分組
# metadata。KIDNEY 刻意同時屬於兩組（ADA CKM 框架原文設計，非工程重複，
# 見架構文件v2 第2節命名統一總表裁定）。純資訊揭露用途，不影響 finding
# 產生邏輯。
DOMAIN_DISPLAY_GROUPS: dict[ClinicalDomain, tuple[str, ...]] = {
    ClinicalDomain.KIDNEY: ("microvascular", "cardiometabolic"),
    ClinicalDomain.EYE: ("microvascular",),
    ClinicalDomain.NEUROPATHY: ("microvascular",),
    ClinicalDomain.FOOT: ("microvascular",),
    ClinicalDomain.ASCVD: ("macrovascular",),
    ClinicalDomain.CEREBROVASCULAR: ("macrovascular",),
    ClinicalDomain.PAD: ("macrovascular",),
    ClinicalDomain.HEART_FAILURE: ("cardiometabolic",),
    ClinicalDomain.LIVER: ("cardiometabolic",),
    ClinicalDomain.WEIGHT_OBESITY: ("cardiometabolic",),
    ClinicalDomain.GLYCEMIC_CONTROL: (),  # 品質指標型 domain，不進複雜度地圖三分組
    ClinicalDomain.HYPOGLYCEMIA: (),
    ClinicalDomain.BLOOD_PRESSURE: (),
}


class ClinicalStatus(str, Enum):
    """★★★ 規格§5 四態，全管線唯一權威定義，逐字對應規格四種安全分級。★★★

    - CONFIRMED：已有明確臨床證據（confirmed disease）。
    - SUSPECTED：檢驗/檢查異常，但仍需確認（suspected/possible disease）。
    - HIGH_RISK：沒有疾病證據，但 calculator 顯示未來事件風險高。
    - CARE_GAP：不是疾病也不是高風險，而是「缺資料/該做的事還沒做」。
    """

    CONFIRMED = "confirmed"
    SUSPECTED = "suspected"
    HIGH_RISK = "high_risk"
    CARE_GAP = "care_gap"


class SourceSystem(str, Enum):
    HIS = "HIS"
    LIS = "LIS"
    CPOE = "CPOE"
    CVIS = "CVIS"
    PACS = "PACS"
    OPHTHALMOLOGY = "OPHTHALMOLOGY"
    FOOT_NEURO = "FOOT_NEURO"
    VASCULAR_LAB = "VASCULAR_LAB"
    ADMIN = "ADMIN"
    CALCULATOR = "CALCULATOR"
    DERIVED = "DERIVED"


@dataclass(frozen=True)
class EvidenceItem:
    """規格§26 Evidence Widget 逐筆列點需求；`value` 統一字串化以容納
    數值/類別型結果。"""

    label: str
    value: str
    unit: Optional[str] = None
    observed_date: Optional[date] = None
    source: SourceSystem = SourceSystem.DERIVED


LOCAL_VALIDATION_WARNING = (
    "此模型並非以台灣族群建立，適合 risk communication / risk stratification，"
    "台灣正式進入 decision threshold 前應完成 local calibration / validation"
    "（OpenClaw for Diabetes HIS §37）"
)


@dataclass(frozen=True)
class ModelProvenance:
    """Tier B calculator 必填欄位；`clinical_data_object.py`/`calculators`
    兩層共用唯一定義（`calculators/base.py` 直接 import，不重新宣告）。"""

    model_name: str
    original_population: str
    taiwan_local_validation_status: str = "not_locally_validated"
    # Literal["not_locally_validated","local_calibration_in_progress","locally_validated"]
    # 保留為 str（而非嚴格 Literal 型別檢查），與既有型別風格一致（見
    # pipeline_models.py 既有 Literal 慣例僅用於 dataclass 欄位本身，此處為
    # 求 dataclass 相等比較/序列化簡便，仍以字串常數約束，違反值於
    # __post_init__ 拋錯）。
    spec_reference: str = ""
    warning: str = LOCAL_VALIDATION_WARNING

    _VALID_STATUSES = (
        "not_locally_validated",
        "local_calibration_in_progress",
        "locally_validated",
    )

    def __post_init__(self) -> None:
        if self.taiwan_local_validation_status not in self._VALID_STATUSES:
            raise ValueError(
                f"taiwan_local_validation_status 必須是 {self._VALID_STATUSES} 之一，"
                f"收到: {self.taiwan_local_validation_status!r}"
            )
        if not self.model_name:
            raise ValueError("ModelProvenance.model_name 不可為空字串")
        if not self.original_population:
            raise ValueError("ModelProvenance.original_population 不可為空字串（鐵律2：Tier B 必須明示原始驗證族群）")

    @property
    def locally_validated(self) -> bool:
        return self.taiwan_local_validation_status == "locally_validated"


@dataclass(frozen=True)
class ClinicalFinding:
    """★★★ 規格§30 OpenClaw Clinical Data Object，全管線唯一權威型別。★★★

    Layer2（`clinical_state.derive_clinical_state()`）建立時只保證填好
    `finding_id`/`patient_id`/`domain`/`condition`/`status`/`severity`/
    `evidence`/`source`/`date`/`calculator`/`calculator_version`/
    `model_provenance`/`is_placeholder`；`guideline`/`recommendation`/
    `action_status`/`clinician_response` 留給 Layer5/6 透過
    `compose_clinical_data_objects()`（見下）以 `dataclasses.replace()`
    產生衍生版本填入（frozen，禁止原地 mutate，呼應規格§36 audit trail
    「保留每一版判斷」精神）。
    """

    finding_id: str
    patient_id: str
    domain: ClinicalDomain
    condition: str
    status: ClinicalStatus
    severity: Optional[str] = None
    evidence: tuple[EvidenceItem, ...] = ()
    source: SourceSystem = SourceSystem.DERIVED
    date: date = None  # type: ignore[assignment]  # 必填，dataclass 預設值僅為型別工具妥協
    calculator: Optional[str] = None
    calculator_version: Optional[str] = None
    guideline: Optional[str] = None
    recommendation: Optional[str] = None
    action_status: str = "not_yet_reviewed"
    clinician_response: Optional[str] = None
    model_provenance: Optional[ModelProvenance] = None
    is_placeholder: bool = False
    generated_at: datetime = None  # type: ignore[assignment]  # 必填，由建構端一律傳入 datetime.now()

    def __post_init__(self) -> None:
        if self.date is None:
            raise ValueError("ClinicalFinding.date 為必填欄位，不可為 None")
        if self.generated_at is None:
            raise ValueError("ClinicalFinding.generated_at 為必填欄位，不可為 None")


def compose_clinical_data_objects(
    state: "PatientClinicalState",
    guideline_report: "GuidelineRecommendationReport | None" = None,
    decision_record: "PhysicianDecisionRecord | None" = None,
) -> list[ClinicalFinding]:
    """把 Layer2 base findings 與 Layer5 guideline 建議、Layer6 醫師決策
    「合成」為最終供 UI/Audit 使用的完整§30 物件清單（以 `finding_id` 為
    join key，`dataclasses.replace()` 產生新版本，不 mutate 原 finding）。

    沒有對應 `GuidelineRecommendation` 命中的 finding 原樣輸出（`guideline`/
    `recommendation` 維持 None，`action_status` 維持 `"not_yet_reviewed"`）；
    一個 finding 若被多條規則命中，取 `priority` 數值最小（最高優先）者。

    ★ 本函式對 `guideline_report`/`decision_record` 的實際型別採鴨子定型
    （duck typing）存取，而非在執行期 import `guideline_recommendation.py`/
    `physician_decision.py`——因為這兩個模組本階段（v2 core layer 落地）尚未
    完成§3.7/§3.9 所述的欄位擴充（`related_finding_id`/`Reviewable`），先以
    寬鬆介面實作，待該擴充落地後不需要改動本函式的呼叫端。目前只要
    `guideline_report.recommendations` 內每個元素具備
    `related_finding_id`/`guideline_id`/`title`/`priority` 屬性即可運作；
    若整份 report 不含任何 `related_finding_id` 屬性，一律視為「沒有可合成
    的建議」，回傳與輸入相同的 findings（不拋錯，向下相容尚未擴充
    guideline_recommendation.py 的呼叫端）。
    """

    if guideline_report is None:
        return list(state.findings)

    # ★ 修正（Codex 審閱發現）：guideline_recommendation.RecommendationPriority
    # 是 `str, Enum`（值為 "ROUTINE"/"PRIORITY"/"URGENT" 字串，不是數字），
    # 原本 `isinstance(priority_value, (int, float))` 對任何合法優先權都是
    # False，導致每一筆都落入 999 這個 fallback——「取 priority 最高者」的
    # 排序邏輯形同虛設，實際上永遠是「先出現者贏」。改用具名字串→排序值
    # 對照表（數值越小代表優先權越高，語意同 URGENT 最先處理）。本檔案
    # 刻意不 import guideline_recommendation.RecommendationPriority（避免
    # 對 Enum 具體型別產生執行期依賴，維持鴨子定型設計），改用字串比對，
    # 未來若 medication_intelligence.py 或其他來源用同名字串值的優先權
    # Enum，同一份對照表仍然適用。
    _PRIORITY_RANK = {"URGENT": 0, "PRIORITY": 1, "ROUTINE": 2}

    recommendations = getattr(guideline_report, "recommendations", ())
    # finding_id -> 目前選中的 (priority, recommendation)
    best_by_finding_id: dict[str, tuple[int, object]] = {}
    for rec in recommendations:
        related_finding_id = getattr(rec, "related_finding_id", None)
        if not related_finding_id:
            continue
        priority_obj = getattr(rec, "priority", None)
        priority_value = getattr(priority_obj, "value", priority_obj)
        if isinstance(priority_value, (int, float)):
            rank = priority_value
        elif isinstance(priority_value, str) and priority_value in _PRIORITY_RANK:
            rank = _PRIORITY_RANK[priority_value]
        else:
            rank = 999  # 未知的優先權型別/值，視為最低優先權，不可靜默排最前
        current = best_by_finding_id.get(related_finding_id)
        if current is None or rank < current[0]:
            best_by_finding_id[related_finding_id] = (rank, rec)

    decisions_by_recommendation_id: dict[str, object] = {}
    if decision_record is not None:
        # ★ 修正（Codex 審閱發現）：`decision_record.decisions` 是
        # `dict[str, PhysicianDecision]`，直接 `for decision in ...decisions`
        # 只會走到字典的 key（字串），永遠不會命中下方任何一次 decision
        # 合成——必須 `.values()`。
        for decision in getattr(decision_record, "decisions", {}).values():
            rec_id = getattr(decision, "recommendation_id", None)
            if rec_id:
                decisions_by_recommendation_id[rec_id] = decision

    composed: list[ClinicalFinding] = []
    for finding in state.findings:
        hit = best_by_finding_id.get(finding.finding_id)
        if hit is None:
            composed.append(finding)
            continue
        _, rec = hit
        updates: dict[str, object] = {
            "guideline": getattr(rec, "guideline_id", None) or getattr(rec, "guideline", None),
            "recommendation": getattr(rec, "title", None),
        }
        rec_id = getattr(rec, "recommendation_id", None) or getattr(rec, "rule_id", None)
        decision = decisions_by_recommendation_id.get(rec_id) if rec_id else None
        if decision is not None:
            decision_status = getattr(decision, "status", None)
            updates["action_status"] = getattr(decision_status, "value", decision_status) or finding.action_status
            # ★ 修正（Codex 審閱發現）：`PhysicianDecision` 實際欄位是
            # `free_text_note`/`modified_action_text`，不是 `note`/
            # `modified_text`——原字串永遠對不上，clinician_response 恆 None。
            updates["clinician_response"] = getattr(decision, "free_text_note", None) or getattr(
                decision, "modified_action_text", None
            )
        composed.append(replace(finding, **updates))

    return composed
