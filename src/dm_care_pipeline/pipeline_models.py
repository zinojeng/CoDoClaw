"""
第1站輸出型別：全管線唯一共同輸入 `PatientClinicalProfile`。

依 docs/臨床決策支援管線設計.md 3.1 節整合裁定：本檔案獨立於
`data_integration.py`（動詞命名的模組不適合放型別定義），供第2~9站
import。刻意不命名為 `models.py`，避免與 `dm_eligibility/models.py` 在
import 時混淆。

設計原則（沿用 `dm_eligibility/models.py` 既有風格）：
- `PatientClinicalProfile` 是唯讀、自我完備的物件，composition 持有
  `enrollment_state: PatientEnrollmentState`（Part1 物件參照，不複製欄位）。
- 任何「資料不足/依據不足」一律透過 `DataGapFlag` / `integration_warnings`
  顯式回報，不得靜默假設。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Optional

from dm_eligibility.models import EligibilityReport, PatientEnrollmentState, PhysicianStatus

from .clinical_data_layer import (
    AdministrativeCareStatus,
    CardiacImagingFinding,
    ClinicalDataSourceRegistry,
    EncounterUtilizationRecord,
    FootNeuroExam,
    HypoglycemiaEventRecord,
    ImagingStudyRef,
    OphthalmologyFinding,
    ProcedureRecord,
    VascularExam,
    VitalSignObservation,
)


@dataclass(frozen=True)
class DataGapFlag:
    """顯式回報一項資料缺漏/過期/未知，供下游站點決定是否要提示人工介入。"""

    source: str  # 例如 'lab_results:09006C'、'ckd_assessments'、'age_years'
    status: Literal["missing", "stale", "unknown"]
    detail: str
    relevant_downstream_stages: tuple[str, ...] = ()


@dataclass
class ClinicalProfileConfig:
    """本 Config 中除 `auto_run_eligibility_engine_if_missing` 外，所有欄位
    皆屬【非規格書條文，工程實作補充假設】——P14/P7 規格書聚焦於各照護碼
    本身的檢驗窗口（40/60/180天），並未定義「資料整合階段」這種概括性的
    診斷/用藥回溯窗口，以下數值為工程保守預設，正式上線前應由臨床端確認。
    """

    quality_monitoring_lab_staleness_days: int = 180  # 規格書明文：P14 spec (d) 末段 180天強制排程
    default_lab_staleness_days: int = 180  # TODO：品質監測四項以外檢驗項目，規格未定義，暫借用同一值
    diagnosis_lookback_days: int = 365  # TODO：規格書無此類概括回溯窗口，工程保守預設
    medication_lookback_days: int = 365  # TODO：同上
    auto_run_eligibility_engine_if_missing: bool = False  # 工程決策：預設不代呼叫端觸發 Part1 引擎


@dataclass
class PatientClinicalProfile:
    """全管線唯一共同輸入。唯讀、自我完備；第2~9站一律以它為第一參數。

    呼叫端不應直接修改本物件的欄位——各站規則一律將其視為唯讀輸入。
    """

    patient_id: str
    as_of_date: date
    enrollment_state: PatientEnrollmentState  # composition：直接持有 Part1 物件參照，不複製欄位
    eligibility_report: Optional[EligibilityReport] = None
    physician: Optional[PhysicianStatus] = None
    eligibility_report_as_of_mismatch: bool = False
    lab_series_by_item: dict[str, tuple] = field(default_factory=dict)  # item_code(大寫) → LabResult 序列（新到舊）
    active_diagnosis_codes: frozenset[str] = frozenset()  # 完整 ICD-10 碼（非3碼前綴），diagnosis_lookback_days 窗口內
    active_medication_atc_codes: frozenset[str] = frozenset()
    data_gaps: list[DataGapFlag] = field(default_factory=list)
    integration_warnings: list[str] = field(default_factory=list)

    # --- v2 新增（架構文件v2 3.1節）：clinical_data_layer.py 型別的容器
    # 欄位，皆預設空 tuple/None，向下相容 v1 既有呼叫端（見 data_integration.py
    # build_patient_clinical_profile() 對應的 keyword-only 參數）。---
    vital_signs: tuple[VitalSignObservation, ...] = ()
    ophthalmology_findings: tuple[OphthalmologyFinding, ...] = ()
    cardiac_imaging: tuple[CardiacImagingFinding, ...] = ()
    foot_neuro_exams: tuple[FootNeuroExam, ...] = ()
    vascular_exams: tuple[VascularExam, ...] = ()
    imaging_studies: tuple[ImagingStudyRef, ...] = ()
    hypoglycemia_events: tuple[HypoglycemiaEventRecord, ...] = ()
    procedures: tuple[ProcedureRecord, ...] = ()
    encounter_utilization: tuple[EncounterUtilizationRecord, ...] = ()
    administrative_status: Optional[AdministrativeCareStatus] = None
    data_source_registry: ClinicalDataSourceRegistry = field(default_factory=ClinicalDataSourceRegistry)
    # ★ 本次整合唯一一個不屬於 clinical_data_layer.py 型別、直接掛在
    # PatientClinicalProfile 上的新原始欄位（PREVENT/Legacy ASCVD PCE/KFRE
    # 皆需要）。生理性別 vs 病歷登記性別的定義本身待人工裁定，見架構文件v2
    # 第4節 open_questions。
    sex: Optional[Literal["male", "female", "intersex_unspecified"]] = None
