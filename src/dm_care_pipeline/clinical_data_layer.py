"""
【Layer 1 擴充】規格§3 Layer1 的 dm_care_pipeline 擴充：既有 `dm_eligibility.models`
未涵蓋的原始臨床資料容器（生命徵象、眼科/心臟影像、足部/血管檢查、影像研究、
低血糖事件、行政照護狀態等）。

★★★ 鐵律7 ★★★：本檔案全部 `frozen`，不做任何臨床判讀（good/poor、
LOPS 是否成立、風險高低等一律留給 Layer2 `clinical_state.py`/Layer3
`calculators/`），只做「這筆原始資料是什麼」的顯性容器；`FootNeuroExam`
即使包含 monofilament/vibration 等原始檢查結果，LOPS 判定邏輯仍留給
`calculators/iwgdf_foot.py`。

★★★ 鐵律6（不可靜默假設「沒查=沒事」）落地 ★★★：`SourceSystemStatus`
三態明確區分「系統未介接」/「已查詢確認無資料」/「已查詢有資料」；
`ClinicalDataSourceRegistry` 逐系統記錄目前介接狀態，供
`clinical_state.derive_clinical_state()` 據以決定 `TrafficLight.GRAY`
（見架構文件v2 3.3節）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Literal, Optional


class SourceSystemStatus(str, Enum):
    """★ 鐵律6 的型別層落地：『沒查』與『查了確認沒事』是兩件事，不可混淆。"""

    NOT_INTEGRATED = "not_integrated"  # 系統尚未介接，禁止推斷任何陰性/正常結論
    INTEGRATED_NO_DATA = "integrated_no_data"  # 已查詢、確認無資料（可視為「確認陰性」的必要條件之一）
    INTEGRATED_HAS_DATA = "integrated_has_data"


@dataclass(frozen=True)
class ClinicalDataSourceRegistry:
    """逐一來源系統的介接狀態，供 `clinical_state.py` 判斷 domain 是否可標
    `TrafficLight.GREEN`（必須 `INTEGRATED_HAS_DATA` 且無異常 finding）或
    `TrafficLight.GRAY`（`NOT_INTEGRATED` 時優先權高於「查無 finding」）。"""

    his_vitals: SourceSystemStatus = SourceSystemStatus.NOT_INTEGRATED
    lis_extended: SourceSystemStatus = SourceSystemStatus.NOT_INTEGRATED  # AST/ALT/Platelet/BNP/NT-proBNP 等擴充LIS項目
    cvis: SourceSystemStatus = SourceSystemStatus.NOT_INTEGRATED
    pacs: SourceSystemStatus = SourceSystemStatus.NOT_INTEGRATED
    ophthalmology: SourceSystemStatus = SourceSystemStatus.NOT_INTEGRATED
    foot_neuro: SourceSystemStatus = SourceSystemStatus.NOT_INTEGRATED
    vascular_lab: SourceSystemStatus = SourceSystemStatus.NOT_INTEGRATED
    admin: SourceSystemStatus = SourceSystemStatus.NOT_INTEGRATED
    last_queried_at: dict[str, date] = field(default_factory=dict)  # 系統名→最後查詢日

    def all_not_integrated(self) -> bool:
        """`data_integration.py` 用於判斷是否需要對整個 Layer1 擴充資料下
        `DataGapFlag`（工程便利函式，非規格逐字條文）。"""
        return all(
            status == SourceSystemStatus.NOT_INTEGRATED
            for status in (
                self.his_vitals,
                self.lis_extended,
                self.cvis,
                self.pacs,
                self.ophthalmology,
                self.foot_neuro,
                self.vascular_lab,
                self.admin,
            )
        )


class SmokingStatus(str, Enum):
    NEVER = "never"
    FORMER = "former"
    CURRENT = "current"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class VitalSignObservation:
    observation_date: date
    systolic_bp: Optional[float] = None
    diastolic_bp: Optional[float] = None
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    bmi: Optional[float] = None  # 可由 height/weight 換算，或 HIS 直接提供
    smoking_status: SmokingStatus = SmokingStatus.UNKNOWN
    source: str = "HIS"


@dataclass(frozen=True)
class OphthalmologyFinding:
    exam_date: date
    method: Literal["manual", "VeriSee_AI", "other"]
    dr_classification: Literal["none", "mild_npdr", "moderate_npdr", "severe_npdr", "pdr", "ungradable"]
    dme_present: Optional[bool] = None
    laterality: Optional[Literal["OD", "OS", "OU"]] = None
    diagnosis_text: str = ""
    source: str = "OPHTHALMOLOGY"


@dataclass(frozen=True)
class ProcedureRecord:
    procedure_code: Optional[str]  # 允許為空、自由文字（非結構化醫令系統過渡容器）
    procedure_name: str
    procedure_date: date
    source: str


@dataclass(frozen=True)
class CardiacImagingFinding:
    study_date: date
    modality: Literal["ECG", "ECHO"]
    qrs_duration_ms: Optional[float] = None
    lvef_percent: Optional[float] = None
    diastolic_dysfunction_grade: Optional[str] = None
    structural_heart_disease_present: Optional[bool] = None
    source: str = "CVIS"


@dataclass(frozen=True)
class UlcerRecord:
    event_date: date
    resolved: bool
    laterality: Optional[Literal["L", "R", "bilateral"]] = None
    source: str = "FOOT_NEURO"


@dataclass(frozen=True)
class AmputationRecord:
    event_date: date
    laterality: Optional[Literal["L", "R", "bilateral"]] = None
    level: Optional[str] = None
    source: str = "FOOT_NEURO"


@dataclass(frozen=True)
class FootNeuroExam:
    exam_date: date
    monofilament_result_left: Literal["normal", "abnormal", "not_tested"] = "not_tested"
    monofilament_result_right: Literal["normal", "abnormal", "not_tested"] = "not_tested"
    vibration_result: Literal["normal", "abnormal", "not_tested"] = "not_tested"
    temperature_pinprick_result: Literal["normal", "abnormal", "not_tested"] = "not_tested"
    ncv_result: Optional[str] = None
    foot_deformity_present: Optional[bool] = None  # 含 Charcot foot
    ulcer_history: tuple[UlcerRecord, ...] = ()
    amputation_history: tuple[AmputationRecord, ...] = ()
    source: str = "FOOT_NEURO"
    # LOPS（loss of protective sensation）判定邏輯留給 Layer3 IWGDF calculator，
    # 本類別只提供原始欄位（鐵律7：資料容器與判讀邏輯分離）。


@dataclass(frozen=True)
class VascularExam:
    exam_date: date
    abi_right: Optional[float] = None
    abi_left: Optional[float] = None
    tbi_right: Optional[float] = None
    tbi_left: Optional[float] = None
    doppler_summary: Optional[str] = None
    angiography_summary: Optional[str] = None
    claudication_present: Optional[bool] = None
    pedal_pulse_present: Optional[bool] = None
    revascularization_history: tuple[ProcedureRecord, ...] = ()
    source: str = "VASCULAR_LAB"


@dataclass(frozen=True)
class ImagingStudyRef:
    study_date: date
    modality: str
    body_region: Literal["liver", "cardiac", "vascular", "other"]
    report_text: str = ""
    structured_findings: dict = field(default_factory=dict)
    source: str = "PACS"


@dataclass(frozen=True)
class HypoglycemiaEventRecord:
    event_date: date
    severity: Literal["level1", "level2", "level3"]
    setting: Literal["self_reported", "outpatient", "ed", "inpatient"]
    source: str = "HIS"


@dataclass(frozen=True)
class ReferralRecord:
    specialty: str
    ordered_date: date
    status: Literal["ordered", "completed", "cancelled"]


@dataclass(frozen=True)
class AdministrativeCareStatus:
    diabetes_shared_care_enrolled: Optional[bool] = None
    upcoming_appointment_date: Optional[date] = None
    pending_referrals: tuple[ReferralRecord, ...] = ()
    diabetes_educator_involved: Optional[bool] = None
    dietitian_involved: Optional[bool] = None
    source: str = "ADMIN"
    # ckd_p4p_enrolled 刻意不重複維護：改由呼叫端讀
    # profile.eligibility_report.eligible_codes() 推導（dm_eligibility EligibilityReport
    # 已是收案狀態的權威來源，見架構文件v2 §5節整合原則）。


@dataclass(frozen=True)
class EncounterUtilizationRecord:
    """★ 新增、平行於 dm_eligibility `Encounter`（凍結，不修改）的「就醫場域分類」
    容器，供 Karter Hypoglycemia Tier B calculator 之
    `ed_visits_prior_12mo`/`prior_hypo_related_ed_or_hosp` 使用。dm_eligibility
    `Encounter` 本身沒有門診/急診/住院分類欄位，這是唯讀的平行擴充，不改動
    dm_eligibility 既有物件（見架構文件v2 第4/5節 open_questions）。"""

    encounter_id: str  # 建議與 dm_eligibility Encounter.encounter_id 對應，供交叉核對
    visit_date: date
    setting: Literal["outpatient", "ed", "inpatient"]
    hypoglycemia_related: Optional[bool] = None
    source: str = "HIS"
