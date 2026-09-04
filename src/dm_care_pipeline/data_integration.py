"""
【第1站】資料整合 — 把 Part1 的 `PatientEnrollmentState`（+ 可選的
`EligibilityReport`/`PhysicianStatus`）彙整成全管線唯一共同輸入
`PatientClinicalProfile`。

本站不做任何臨床判讀（良好/不良、風險高低等一律留給第2站起的各站），
只做「彙整、分組、標記缺漏」。任何無法確定的資料一律產生 `DataGapFlag`
或 `integration_warnings`，不得靜默假設（鐵律5）。
"""

from __future__ import annotations

from datetime import timedelta
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
from .pipeline_models import ClinicalProfileConfig, DataGapFlag, PatientClinicalProfile


def _group_lab_series_by_item(state: PatientEnrollmentState) -> dict[str, tuple]:
    """依 item_code（正規化為大寫）分組，每組內依 result_date 新到舊排序。"""
    by_item: dict[str, list] = {}
    for lr in state.lab_results:
        by_item.setdefault(lr.item_code.upper(), []).append(lr)
    return {code: tuple(sorted(series, key=lambda lr: lr.result_date, reverse=True)) for code, series in by_item.items()}


def _active_diagnosis_codes(state: PatientEnrollmentState, as_of, lookback_days: int) -> frozenset[str]:
    start = as_of - timedelta(days=lookback_days)
    codes: set[str] = set()
    for e in state.encounters_within(start, as_of):
        for d in e.diagnoses:
            codes.add(d.icd10_code.upper())
    return frozenset(codes)


def _active_medication_atc_codes(state: PatientEnrollmentState, as_of, lookback_days: int) -> frozenset[str]:
    start = as_of - timedelta(days=lookback_days)
    codes: set[str] = set()
    for e in state.encounters_within(start, as_of):
        for m in e.medication_orders:
            codes.add(m.atc_code.upper())
    return frozenset(codes)


def build_patient_clinical_profile(
    state: PatientEnrollmentState,
    *,
    eligibility_report: EligibilityReport | None = None,
    physician: PhysicianStatus | None = None,
    config: ClinicalProfileConfig | None = None,
    eligibility_engine=None,  # dm_eligibility.engine.EligibilityEngine | None
    # --- v2 新增（架構文件v2 3.1節）：clinical_data_layer.py 型別的容器
    # keyword-only 參數，皆預設空/None，既有呼叫端零改動即可運作。---
    vital_signs: tuple[VitalSignObservation, ...] = (),
    ophthalmology_findings: tuple[OphthalmologyFinding, ...] = (),
    cardiac_imaging: tuple[CardiacImagingFinding, ...] = (),
    foot_neuro_exams: tuple[FootNeuroExam, ...] = (),
    vascular_exams: tuple[VascularExam, ...] = (),
    imaging_studies: tuple[ImagingStudyRef, ...] = (),
    hypoglycemia_events: tuple[HypoglycemiaEventRecord, ...] = (),
    procedures: tuple[ProcedureRecord, ...] = (),
    encounter_utilization: tuple[EncounterUtilizationRecord, ...] = (),
    administrative_status: Optional[AdministrativeCareStatus] = None,
    data_source_registry: Optional[ClinicalDataSourceRegistry] = None,
    sex: Optional[Literal["male", "female", "intersex_unspecified"]] = None,
) -> PatientClinicalProfile:
    """第1站進入點。不修改任何傳入物件（唯讀）。缺資料一律外顯為
    `DataGapFlag` / `integration_warnings`，不靜默假設、不 raise（不阻斷管線）。

    `eligibility_engine` 僅在 `eligibility_report is None` 且
    `config.auto_run_eligibility_engine_if_missing` 為 True 時才會被呼叫
    （預設 False：不代呼叫端觸發 Part1 引擎，避免本函式產生非預期的
    副作用性計算——是否要重新跑一次收案資格判斷，應由呼叫端顯式決定）。
    """
    cfg = config or ClinicalProfileConfig()
    as_of = state.as_of_date
    data_gaps: list[DataGapFlag] = []
    warnings: list[str] = []

    if eligibility_report is None and cfg.auto_run_eligibility_engine_if_missing and eligibility_engine is not None:
        eligibility_report = eligibility_engine.evaluate(state, physician)

    eligibility_report_as_of_mismatch = False
    if eligibility_report is not None and eligibility_report.as_of_date != as_of:
        eligibility_report_as_of_mismatch = True
        warnings.append(
            f"傳入之 eligibility_report.as_of_date({eligibility_report.as_of_date}) 與 "
            f"state.as_of_date({as_of}) 不一致，下游站點使用時應留意時效性"
        )

    if eligibility_report is None:
        data_gaps.append(
            DataGapFlag(
                source="eligibility_report",
                status="missing",
                detail="未提供 EligibilityReport，且未啟用 auto_run_eligibility_engine_if_missing",
                relevant_downstream_stages=("care_gap", "guideline_recommendation", "followup"),
            )
        )

    if physician is None:
        data_gaps.append(
            DataGapFlag(
                source="physician",
                status="unknown",
                detail="未提供 PhysicianStatus，無法確認醫師停權/雙重資格狀態",
                relevant_downstream_stages=("followup",),
            )
        )

    if state.age_years is None:
        data_gaps.append(
            DataGapFlag(source="age_years", status="unknown", detail="病人年齡未知", relevant_downstream_stages=("risk",))
        )

    if not state.encounters:
        data_gaps.append(
            DataGapFlag(
                source="encounters",
                status="missing",
                detail="查無任何就診紀錄",
                relevant_downstream_stages=("trend_analysis", "complication_identification", "care_gap"),
            )
        )

    if not state.lab_results:
        data_gaps.append(
            DataGapFlag(
                source="lab_results",
                status="missing",
                detail="查無任何檢驗結果",
                relevant_downstream_stages=("trend_analysis", "care_gap"),
            )
        )

    lab_series_by_item = _group_lab_series_by_item(state)
    active_diagnosis_codes = _active_diagnosis_codes(state, as_of, cfg.diagnosis_lookback_days)
    active_medication_atc_codes = _active_medication_atc_codes(state, as_of, cfg.medication_lookback_days)

    resolved_registry = data_source_registry or ClinicalDataSourceRegistry()
    if resolved_registry.all_not_integrated():
        data_gaps.append(
            DataGapFlag(
                source="clinical_data_layer",
                status="missing",
                detail="ClinicalDataSourceRegistry 所有來源系統皆為 NOT_INTEGRATED，"
                "vital_signs/ophthalmology_findings/cardiac_imaging/foot_neuro_exams/"
                "vascular_exams 等 v2 擴充資料尚未介接",
                relevant_downstream_stages=("clinical_state", "calculators"),
            )
        )

    return PatientClinicalProfile(
        patient_id=state.patient_id,
        as_of_date=as_of,
        enrollment_state=state,
        eligibility_report=eligibility_report,
        physician=physician,
        eligibility_report_as_of_mismatch=eligibility_report_as_of_mismatch,
        lab_series_by_item=lab_series_by_item,
        active_diagnosis_codes=active_diagnosis_codes,
        active_medication_atc_codes=active_medication_atc_codes,
        data_gaps=data_gaps,
        integration_warnings=warnings,
        vital_signs=vital_signs,
        ophthalmology_findings=ophthalmology_findings,
        cardiac_imaging=cardiac_imaging,
        foot_neuro_exams=foot_neuro_exams,
        vascular_exams=vascular_exams,
        imaging_studies=imaging_studies,
        hypoglycemia_events=hypoglycemia_events,
        procedures=procedures,
        encounter_utilization=encounter_utilization,
        administrative_status=administrative_status,
        data_source_registry=resolved_registry,
        sex=sex,
    )
