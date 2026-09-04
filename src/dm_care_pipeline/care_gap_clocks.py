"""
【Care-Gap Agent】規格§18「三時鐘」（Clinical Clock / P4P Clock /
Patient-Specific Clock）與§19「Advanced Screening」的具體落地。

★★★ 鐵律1 ★★★：三時鐘中只有 P4P Clock（沿用既有 `care_gap.py`）與
`IWGDFFootClockRule`（沿用 `calculators.iwgdf_foot.
IWGDF_FOLLOWUP_INTERVAL_DAYS`）有規格書逐字數字依據；Clinical Clock 的
「年度預設頻率」與 `RetinopathySeverityClockRule`/
`CKDMonitoringFrequencyClockRule` 皆為工程保守預設（`is_placeholder_interval
=True`），不可冒充已驗證排程規則。

★★★ 鐵律7 ★★★：P4P Clock 直接包裝既有 `care_gap.assess_care_gaps()` 的輸出
（零重算）；IWGDF 分級→頻率對照唯一權威來源仍是
`calculators.iwgdf_foot.IWGDF_FOLLOWUP_INTERVAL_DAYS`，本檔案只 import 使用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Literal, Mapping, Optional, Protocol

from .calculators.base import CalculatorExecutionStatus, CalculatorResult
from .calculators.iwgdf_foot import IWGDF_FOLLOWUP_INTERVAL_DAYS
from .care_gap import CareGapReport
from .clinical_data_layer import SmokingStatus
from .clinical_data_object import ClinicalDomain, ClinicalFinding, ClinicalStatus, EvidenceItem, SourceSystem
from .clinical_state import PatientClinicalState
from .pipeline_models import DataGapFlag, PatientClinicalProfile

# ---------------------------------------------------------------------------
# ClockEvaluation / to_finding()
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClockEvaluation:
    item_code: str
    description: str
    clock_type: Literal["CLINICAL", "P4P", "PATIENT_SPECIFIC"]
    last_performed_date: Optional[date]
    interval_days_range: tuple[int, int]  # (min,max)；單一值時 min==max
    next_due_earliest: Optional[date]
    next_due_latest: Optional[date]
    satisfied: bool
    is_placeholder_interval: bool
    guideline: Optional[str] = None
    calculator: Optional[str] = None
    spec_reference: Optional[str] = None


def _finding_id(domain: ClinicalDomain, slug: str, patient_id: str, as_of: date) -> str:
    """穩定 id 建構（規格建議格式 `f"{domain}:{condition}:{patient_id}:{date}"`）。
    ★ 與 `clinical_state._finding_id()` 的差異：本檔案不維護跨呼叫的碰撞
    去重命名空間——每筆 clock/advanced-screening finding 的 `slug` 已包含
    `item_code`/固定規則名稱，同一次 `assess_care_gap_agent()` 呼叫內
    (domain, slug, patient_id, date) 不會重複；若改用模組層級可變全域集合
    做去重，會讓同一輸入的重複呼叫（例如測試重跑）產生不同 id，破壞
    純函式的可重現性，故刻意不做。"""
    return f"{domain.value}:{slug}:{patient_id}:{as_of.isoformat()}"


def to_finding(ev: ClockEvaluation, patient_id: str, domain: ClinicalDomain, as_of: date) -> Optional[ClinicalFinding]:
    """僅 `satisfied=False` 時產生 `ClinicalFinding`（`status=CARE_GAP`）。
    `as_of` 為呼叫端當次評估基準日（`ClockEvaluation` 本身不帶這個欄位，
    比照規格pseudocode 精確欄位表）。"""
    if ev.satisfied:
        return None
    return ClinicalFinding(
        finding_id=_finding_id(domain, f"CLOCK:{ev.item_code}", patient_id, as_of),
        patient_id=patient_id,
        domain=domain,
        condition=ev.description,
        status=ClinicalStatus.CARE_GAP,
        severity="placeholder_interval" if ev.is_placeholder_interval else None,
        evidence=(
            EvidenceItem(
                label="last_performed_date",
                value=ev.last_performed_date.isoformat() if ev.last_performed_date else "無紀錄",
                observed_date=ev.last_performed_date,
                source=SourceSystem.DERIVED,
            ),
        ),
        source=SourceSystem.DERIVED,
        date=as_of,
        calculator=ev.calculator,
        guideline=ev.guideline,
        is_placeholder=ev.is_placeholder_interval,
        generated_at=datetime.now(),
    )


# ---------------------------------------------------------------------------
# Clinical Clock
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClinicalClockRule:
    """`CLINICAL_CLOCK_REGISTRY` 的值型別（規格pseudocode只給出
    `dict[str, "ClinicalClockRule"]` 型別註記，本檔案補上完整定義）。
    HbA1c/血脂/eGFR/UACR/眼底五項走 `lab_item_codes`（沿用既有
    `state.lab_results`，鐵律7）；足部/BP/體重/吸菸四項走
    `data_layer_accessor`（讀 `clinical_data_layer.py` 新型別）。"""

    item_code: str
    description: str
    domain: ClinicalDomain
    interval_days_range: tuple[int, int] = (365, 365)  # 年度預設頻率（規格§18質性敘述，非逐字切點）
    guideline: Optional[str] = None
    spec_reference: Optional[str] = None
    lab_item_codes: tuple[str, ...] = ()
    # profile -> 最近一次執行日期（None=查無紀錄）。與 lab_item_codes 互斥，
    # 恰有一者非空。
    data_layer_accessor: Optional[Callable[[PatientClinicalProfile], Optional[date]]] = None
    # ★ 見模組 docstring：只有 P4P Clock/IWGDFFootClockRule 有逐字數字依據，
    # 走 data_layer_accessor 的四項一律恆 True（年度頻率本身即為工程猜測，
    # 與資料是否存在無關）；走 lab_item_codes 的五項因沿用既有 v1 檢驗窗口
    # 概念但頻率本身（365天）同樣未見規格逐字條文，亦恆 True。
    is_placeholder_interval: bool = True


def _latest_foot_neuro_exam_date(profile: PatientClinicalProfile) -> Optional[date]:
    # ★ 修正（Codex 審閱發現）：過濾未來日期，理由同 _lab_last_performed()。
    candidates = [exam.exam_date for exam in profile.foot_neuro_exams if exam.exam_date <= profile.as_of_date]
    if not candidates:
        return None
    return max(candidates)


def _latest_vital_sign_with(profile: PatientClinicalProfile, predicate: Callable[[object], bool]) -> Optional[date]:
    matching = [v for v in profile.vital_signs if predicate(v) and v.observation_date <= profile.as_of_date]
    if not matching:
        return None
    return max(v.observation_date for v in matching)


CLINICAL_CLOCK_REGISTRY: dict[str, ClinicalClockRule] = {
    "HBA1C": ClinicalClockRule(
        item_code="CLINICAL_CLOCK:HBA1C",
        description="HbA1c 年度追蹤（Clinical Clock，工程年度預設，非P4P 180天強制排程）",
        domain=ClinicalDomain.GLYCEMIC_CONTROL,
        lab_item_codes=("09006C", "09139C"),
        spec_reference="OpenClaw for Diabetes HIS.md §18",
    ),
    "LIPID_PANEL": ClinicalClockRule(
        item_code="CLINICAL_CLOCK:LIPID_PANEL",
        description="血脂四項年度追蹤（Clinical Clock）",
        domain=ClinicalDomain.ASCVD,
        lab_item_codes=("09001C", "09004C", "09043C", "09044C"),
        spec_reference="OpenClaw for Diabetes HIS.md §18",
    ),
    "EGFR": ClinicalClockRule(
        item_code="CLINICAL_CLOCK:EGFR",
        description="腎功能（血清肌酸酐/eGFR）年度追蹤（Clinical Clock）",
        domain=ClinicalDomain.KIDNEY,
        lab_item_codes=("09015C",),
        spec_reference="OpenClaw for Diabetes HIS.md §18",
    ),
    "UACR": ClinicalClockRule(
        item_code="CLINICAL_CLOCK:UACR",
        description="微量白蛋白 UACR 年度追蹤（Clinical Clock）",
        domain=ClinicalDomain.KIDNEY,
        lab_item_codes=("12111C",),
        spec_reference="OpenClaw for Diabetes HIS.md §18",
    ),
    "EYE_EXAM": ClinicalClockRule(
        item_code="CLINICAL_CLOCK:EYE_EXAM",
        description="眼底檢查年度追蹤（Clinical Clock）",
        domain=ClinicalDomain.EYE,
        lab_item_codes=("23501C", "23502C"),
        spec_reference="OpenClaw for Diabetes HIS.md §18",
    ),
    "FOOT_EXAM": ClinicalClockRule(
        item_code="CLINICAL_CLOCK:FOOT_EXAM",
        description="足部檢查年度追蹤（Clinical Clock，走 clinical_data_layer.FootNeuroExam）",
        domain=ClinicalDomain.FOOT,
        data_layer_accessor=_latest_foot_neuro_exam_date,
        spec_reference="OpenClaw for Diabetes HIS.md §18",
    ),
    "BLOOD_PRESSURE": ClinicalClockRule(
        item_code="CLINICAL_CLOCK:BLOOD_PRESSURE",
        description="血壓量測年度追蹤（Clinical Clock，走 clinical_data_layer.VitalSignObservation）",
        domain=ClinicalDomain.BLOOD_PRESSURE,
        data_layer_accessor=lambda p: _latest_vital_sign_with(p, lambda v: v.systolic_bp is not None),
        spec_reference="OpenClaw for Diabetes HIS.md §18",
    ),
    "WEIGHT": ClinicalClockRule(
        item_code="CLINICAL_CLOCK:WEIGHT",
        description="體重量測年度追蹤（Clinical Clock，走 clinical_data_layer.VitalSignObservation）",
        domain=ClinicalDomain.WEIGHT_OBESITY,
        data_layer_accessor=lambda p: _latest_vital_sign_with(p, lambda v: v.weight_kg is not None),
        spec_reference="OpenClaw for Diabetes HIS.md §18",
    ),
    "SMOKING_STATUS": ClinicalClockRule(
        item_code="CLINICAL_CLOCK:SMOKING_STATUS",
        description="吸菸狀態年度追蹤（Clinical Clock，走 clinical_data_layer.VitalSignObservation）",
        # ★ 工程指派：吸菸是 ASCVD 主要風險因子，規格書未明文指定歸類於哪個
        # ClinicalDomain，此處歸入 ASCVD，需臨床端覆核。
        domain=ClinicalDomain.ASCVD,
        data_layer_accessor=lambda p: _latest_vital_sign_with(p, lambda v: v.smoking_status != SmokingStatus.UNKNOWN),
        spec_reference="OpenClaw for Diabetes HIS.md §18",
    ),
}


def _lab_last_performed(profile: PatientClinicalProfile, item_codes: tuple[str, ...]) -> Optional[date]:
    codes_upper = {c.upper() for c in item_codes}
    # ★ 修正（Codex 審閱發現）：原本未過濾「檢驗日期不可晚於
    # profile.as_of_date」，未來日期的檢驗結果會被誤判為「最近一次執行」，
    # 影響 Clinical Clock 的 satisfied/last_performed_date 判斷。
    candidates = [
        lr
        for lr in profile.enrollment_state.lab_results
        if lr.item_code.upper() in codes_upper and lr.result_date <= profile.as_of_date
    ]
    if not candidates:
        return None
    return max(lr.result_date for lr in candidates)


def _evaluate_clinical_clock_rule(rule: ClinicalClockRule, profile: PatientClinicalProfile, as_of: date) -> ClockEvaluation:
    if rule.lab_item_codes:
        last_performed = _lab_last_performed(profile, rule.lab_item_codes)
    else:
        assert rule.data_layer_accessor is not None
        last_performed = rule.data_layer_accessor(profile)

    _, upper = rule.interval_days_range
    # ★ overdue 判定採區間上界，與 calculators.iwgdf_foot 的
    # OVERDUE_USES_UPPER_BOUND 保守選擇一致（避免區間內就過早提醒），非規格
    # 逐字條文。
    satisfied = last_performed is not None and (as_of - last_performed).days <= upper
    next_due_earliest = last_performed + timedelta(days=rule.interval_days_range[0]) if last_performed else None
    next_due_latest = last_performed + timedelta(days=upper) if last_performed else None

    return ClockEvaluation(
        item_code=rule.item_code,
        description=rule.description,
        clock_type="CLINICAL",
        last_performed_date=last_performed,
        interval_days_range=rule.interval_days_range,
        next_due_earliest=next_due_earliest,
        next_due_latest=next_due_latest,
        satisfied=satisfied,
        is_placeholder_interval=rule.is_placeholder_interval,
        guideline=rule.guideline,
        spec_reference=rule.spec_reference,
    )


def clinical_clock_view(
    profile: PatientClinicalProfile, as_of: date, config: "CareGapAgentConfig | None" = None
) -> list[ClockEvaluation]:
    registry = (config.clinical_clock_registry if config else None) or CLINICAL_CLOCK_REGISTRY
    return [_evaluate_clinical_clock_rule(rule, profile, as_of) for rule in registry.values()]


# ---------------------------------------------------------------------------
# P4P Clock
# ---------------------------------------------------------------------------


def p4p_clock_view(care_gap_report: CareGapReport) -> list[ClockEvaluation]:
    """純包裝既有 `care_gap.assess_care_gaps()` 輸出，零重算（鐵律7）。
    P4P 檢驗窗口（`LabRequirement.max_age_days`）是 NHI 規格書逐字條文，
    `is_placeholder_interval` 恆 `False`。"""
    evaluations: list[ClockEvaluation] = []
    for code, items in care_gap_report.by_code.items():
        for item in items:
            last_performed = item.most_recent_ever.result_date if item.most_recent_ever else None
            max_age = item.requirement.max_age_days
            next_due_earliest = last_performed + timedelta(days=max_age) if last_performed else None
            evaluations.append(
                ClockEvaluation(
                    item_code=f"P4P:{code}:{'/'.join(item.source_codes)}",
                    description=item.requirement.description,
                    clock_type="P4P",
                    last_performed_date=last_performed,
                    interval_days_range=(max_age, max_age),
                    next_due_earliest=next_due_earliest,
                    next_due_latest=next_due_earliest,
                    satisfied=item.satisfied,
                    is_placeholder_interval=False,
                    spec_reference=item.spec_reference,
                )
            )
    return evaluations


# ---------------------------------------------------------------------------
# Patient-Specific Clock
# ---------------------------------------------------------------------------


class PatientSpecificClockRule(Protocol):
    def evaluate(
        self, profile: PatientClinicalProfile, clinical_state: PatientClinicalState
    ) -> Optional[ClockEvaluation]: ...


class IWGDFFootClockRule:
    """唯一有完整數字依據的 Patient-Specific Clock 規則（§10）。消費
    `clinical_state` 中 `calculator=="IWGDF_FOOT_RISK"` finding 的
    `severity`（category，見 `clinical_state.py` 對 result_values["category"]
    的透傳補充），對照 `calculators.iwgdf_foot.
    IWGDF_FOLLOWUP_INTERVAL_DAYS`。不可得時優雅降級為 Clinical Clock 之
    通用年度頻率（`is_placeholder_interval=True`）。"""

    def evaluate(self, profile: PatientClinicalProfile, clinical_state: PatientClinicalState) -> Optional[ClockEvaluation]:
        as_of = profile.as_of_date
        last_performed = _latest_foot_neuro_exam_date(profile)
        iwgdf_finding = next(
            (
                f
                for f in clinical_state.by_domain(ClinicalDomain.FOOT)
                if f.calculator == "IWGDF_FOOT_RISK" and f.severity is not None
            ),
            None,
        )
        category: Optional[int] = None
        if iwgdf_finding is not None:
            try:
                category = int(iwgdf_finding.severity)
            except (TypeError, ValueError):
                category = None

        if category is not None and category in IWGDF_FOLLOWUP_INTERVAL_DAYS:
            interval = IWGDF_FOLLOWUP_INTERVAL_DAYS[category]
            is_placeholder = False
            description = f"足部評估追蹤頻率（依 IWGDF Category {category}，§10）"
            spec_reference = "OpenClaw for Diabetes HIS.md §10"
        else:
            # 優雅降級：IWGDF finding 不可得，改用 Clinical Clock 通用年度頻率。
            interval = CLINICAL_CLOCK_REGISTRY["FOOT_EXAM"].interval_days_range
            is_placeholder = True
            description = "足部評估追蹤頻率（IWGDF風險分級不可得，已降級為年度預設頻率）"
            spec_reference = "OpenClaw for Diabetes HIS.md §18（降級路徑，非§10逐字條文）"

        _, upper = interval
        satisfied = last_performed is not None and (as_of - last_performed).days <= upper
        return ClockEvaluation(
            item_code="PATIENT_SPECIFIC:IWGDF_FOOT",
            description=description,
            clock_type="PATIENT_SPECIFIC",
            last_performed_date=last_performed,
            interval_days_range=interval,
            next_due_earliest=last_performed + timedelta(days=interval[0]) if last_performed else None,
            next_due_latest=last_performed + timedelta(days=upper) if last_performed else None,
            satisfied=satisfied,
            is_placeholder_interval=is_placeholder,
            calculator="IWGDF_FOOT_RISK",
            spec_reference=spec_reference,
        )


class RetinopathySeverityClockRule:
    """★ Tier B/待補：規格§18僅質性敘述，無嚴重度分級對照確切追蹤頻率的
    數字表（見架構文件v2 第5節 open_questions#16），鐵律1禁止自行編造切點，
    恆降級為年度預設 + `is_placeholder_interval=True`。"""

    def evaluate(self, profile: PatientClinicalProfile, clinical_state: PatientClinicalState) -> Optional[ClockEvaluation]:
        as_of = profile.as_of_date
        # ★ 修正（Codex 審閱發現）：過濾未來日期，理由同
        # _lab_last_performed()/_latest_foot_neuro_exam_date()——本檔案先前
        # 已修過的同一類 bug，此處是漏網之魚。
        last_performed = max(
            (f.exam_date for f in profile.ophthalmology_findings if f.exam_date <= as_of), default=None
        )
        interval = (365, 365)
        satisfied = last_performed is not None and (as_of - last_performed).days <= interval[1]
        return ClockEvaluation(
            item_code="PATIENT_SPECIFIC:RETINOPATHY_SEVERITY",
            description="視網膜病變嚴重度分級追蹤頻率（規格§18僅質性敘述，無嚴重度對照頻率表，已降級為年度預設）",
            clock_type="PATIENT_SPECIFIC",
            last_performed_date=last_performed,
            interval_days_range=interval,
            next_due_earliest=last_performed + timedelta(days=interval[0]) if last_performed else None,
            next_due_latest=last_performed + timedelta(days=interval[1]) if last_performed else None,
            satisfied=satisfied,
            is_placeholder_interval=True,
            spec_reference="OpenClaw for Diabetes HIS.md §18（open_questions#16 待補）",
        )


class CKDMonitoringFrequencyClockRule:
    """★ 同上：§6.1「依風險增加至每年1-4次」無 G×A 對照確切次數表（見架構
    文件v2 第5節 open_questions#16），恆降級為年度預設 +
    `is_placeholder_interval=True`。"""

    def evaluate(self, profile: PatientClinicalProfile, clinical_state: PatientClinicalState) -> Optional[ClockEvaluation]:
        as_of = profile.as_of_date
        last_performed = _lab_last_performed(profile, ("09015C", "12111C"))
        interval = (365, 365)
        satisfied = last_performed is not None and (as_of - last_performed).days <= interval[1]
        return ClockEvaluation(
            item_code="PATIENT_SPECIFIC:CKD_MONITORING_FREQUENCY",
            description="CKD 監測頻率（規格§6.1「依風險增加至每年1-4次」無G×A對照確切次數表，已降級為年度預設）",
            clock_type="PATIENT_SPECIFIC",
            last_performed_date=last_performed,
            interval_days_range=interval,
            next_due_earliest=last_performed + timedelta(days=interval[0]) if last_performed else None,
            next_due_latest=last_performed + timedelta(days=interval[1]) if last_performed else None,
            satisfied=satisfied,
            is_placeholder_interval=True,
            spec_reference="OpenClaw for Diabetes HIS.md §6.1（open_questions#16 待補）",
        )


DEFAULT_PATIENT_SPECIFIC_CLOCK_RULES: tuple[PatientSpecificClockRule, ...] = (
    IWGDFFootClockRule(),
    RetinopathySeverityClockRule(),
    CKDMonitoringFrequencyClockRule(),
)


# ---------------------------------------------------------------------------
# Advanced Screening（§19）
# ---------------------------------------------------------------------------


def _procedure_ordered(profile: PatientClinicalProfile, keywords: tuple[str, ...]) -> bool:
    """★ 工程啟發式：掃描 `profile.procedures`（`ProcedureRecord.
    procedure_name`）是否包含任一關鍵字，判斷某項進階檢查是否已開立。
    規格書§19未定義如何從結構化資料判斷「NT-proBNP/VCTE 是否已開」，此為
    工程補充，需與 CPOE 介接團隊確認正式判斷依據（見架構文件v2 第5節
    open_questions#18 同類缺口）。

    ★ 修正（Codex 審閱發現）：原本未過濾 `procedure_date <= as_of_date`，
    未來日期的醫令紀錄會被誤判為「已開立」，讓 `advanced_screening_gap()`
    誤以為已經開過而不再提醒（安全方向錯誤：本應提醒卻被壓下）。"""
    lowered_keywords = tuple(k.lower() for k in keywords)
    return any(
        any(kw in (p.procedure_name or "").lower() for kw in lowered_keywords)
        for p in profile.procedures
        if p.procedure_date <= profile.as_of_date
    )


def advanced_screening_gap(
    watch_dm: Optional[CalculatorResult],
    fib4: Optional[CalculatorResult],
    bnp_ordered: bool,
    vcte_ordered: bool,
    *,
    patient_id: str,
    as_of: date,
) -> list[ClinicalFinding]:
    """§19「Advanced screening: WATCH-DM high→考慮NT-proBNP／FIB-4
    elevated→考慮VCTE」。觸發模式本身逐字對應規格§6.2/§6.3路徑敘述，但
    WATCH-DM 上游仍是 Tier B——`execution_status != COMPUTED` 時本規則不
    觸發任何 high_risk 建議，只忠實呈現「尚未可用」，不得把「未驗證」誤判
    為「正常」（鐵律2）：因此 WATCH-DM 分支在目前系統下（Tier B 恆非
    COMPUTED）實際上永遠不會產生 finding，等未來真正驗證過的計算服務
    上線後才會啟用——這是刻意的、非缺陷。"""
    out: list[ClinicalFinding] = []
    generated_at = datetime.now()

    if watch_dm is not None and watch_dm.execution_status == CalculatorExecutionStatus.COMPUTED:
        if watch_dm.clinical_status == ClinicalStatus.HIGH_RISK and not bnp_ordered:
            out.append(
                ClinicalFinding(
                    finding_id=_finding_id(ClinicalDomain.HEART_FAILURE, "ADV_SCREEN:WATCH_DM_NTPROBNP", patient_id, as_of),
                    patient_id=patient_id,
                    domain=ClinicalDomain.HEART_FAILURE,
                    condition="WATCH-DM 高風險，建議安排 NT-proBNP advanced screening",
                    status=ClinicalStatus.CARE_GAP,
                    source=SourceSystem.CALCULATOR,
                    date=as_of,
                    calculator="WATCH_DM",
                    guideline="OpenClaw for Diabetes HIS.md §19",
                    generated_at=generated_at,
                )
            )
    # watch_dm is None 或 execution_status != COMPUTED：不產生任何 finding
    # （既非異常也非正常，屬「尚無法評估」，交由 domain_summaries 的既有
    # CARE_GAP/GRAY 呈現，本函式不重複產生）。

    if fib4 is not None and fib4.execution_status == CalculatorExecutionStatus.COMPUTED:
        if fib4.clinical_status == ClinicalStatus.SUSPECTED and not vcte_ordered:
            out.append(
                ClinicalFinding(
                    finding_id=_finding_id(ClinicalDomain.LIVER, "ADV_SCREEN:FIB4_VCTE", patient_id, as_of),
                    patient_id=patient_id,
                    domain=ClinicalDomain.LIVER,
                    condition="FIB-4 elevated，建議安排 VCTE/FibroScan 或 ELF、hepatology pathway",
                    status=ClinicalStatus.CARE_GAP,
                    source=SourceSystem.CALCULATOR,
                    date=as_of,
                    calculator="FIB4",
                    guideline="OpenClaw for Diabetes HIS.md §19",
                    generated_at=generated_at,
                )
            )

    return out


# ---------------------------------------------------------------------------
# CareGapAgentReport / assess_care_gap_agent()
# ---------------------------------------------------------------------------


@dataclass
class CareGapAgentConfig:
    clinical_clock_registry: Optional[dict[str, ClinicalClockRule]] = None
    patient_specific_clock_rules: tuple[PatientSpecificClockRule, ...] = DEFAULT_PATIENT_SPECIFIC_CLOCK_RULES
    bnp_ordered_keywords: tuple[str, ...] = ("bnp", "nt-probnp", "ntprobnp")
    vcte_ordered_keywords: tuple[str, ...] = ("vcte", "fibroscan", "elastography")


@dataclass
class CareGapAgentReport:
    patient_id: str
    as_of_date: date
    clinical_clock: list[ClockEvaluation]
    p4p_clock: list[ClockEvaluation]
    patient_specific_clock: list[ClockEvaluation]
    advanced_screening_gaps: list[ClinicalFinding]
    warnings: list[str] = field(default_factory=list)
    data_gaps: list[DataGapFlag] = field(default_factory=list)


def assess_care_gap_agent(
    profile: PatientClinicalProfile,
    clinical_state: PatientClinicalState,
    care_gap_report: CareGapReport,
    calculator_results: Mapping[str, CalculatorResult] | None = None,
    config: Optional[CareGapAgentConfig] = None,
) -> CareGapAgentReport:
    """C2（P4P Clock）直接吃既有 `care_gap_report`（不重跑）；C1（Clinical
    Clock）/C3（Patient-Specific Clock）自行評估；C4（Advanced Screening）
    另組。★ 與規格pseudocode的一處刻意偏離：`calculator_results` 預設值
    改為 `None`（同 `clinical_state.derive_clinical_state()`，理由同檔，
    修正 pseudocode `= ()` 本身的筆誤）。"""
    cfg = config or CareGapAgentConfig()
    calc_results = calculator_results or {}
    as_of = profile.as_of_date

    clinical_clock = clinical_clock_view(profile, as_of, cfg)
    p4p_clock = p4p_clock_view(care_gap_report)
    patient_specific_clock = [
        ev for ev in (rule.evaluate(profile, clinical_state) for rule in cfg.patient_specific_clock_rules) if ev is not None
    ]

    bnp_ordered = _procedure_ordered(profile, cfg.bnp_ordered_keywords)
    vcte_ordered = _procedure_ordered(profile, cfg.vcte_ordered_keywords)
    advanced_screening_gaps = advanced_screening_gap(
        calc_results.get("WATCH_DM"),
        calc_results.get("FIB4"),
        bnp_ordered=bnp_ordered,
        vcte_ordered=vcte_ordered,
        patient_id=profile.patient_id,
        as_of=as_of,
    )

    warnings: list[str] = []
    data_gaps: list[DataGapFlag] = []
    for ev in clinical_clock + patient_specific_clock:
        if ev.is_placeholder_interval:
            warnings.append(
                f"{ev.item_code}：追蹤頻率 {ev.interval_days_range} 為工程保守預設，非規格書逐字切點，需臨床端覆核"
            )
        if ev.last_performed_date is None:
            data_gaps.append(
                DataGapFlag(
                    source=f"care_gap_clocks:{ev.item_code}",
                    status="missing",
                    detail=f"{ev.description}：查無任何執行紀錄，無法判斷是否逾期",
                    relevant_downstream_stages=("pre_visit_brief",),
                )
            )

    return CareGapAgentReport(
        patient_id=profile.patient_id,
        as_of_date=as_of,
        clinical_clock=clinical_clock,
        p4p_clock=p4p_clock,
        patient_specific_clock=patient_specific_clock,
        advanced_screening_gaps=advanced_screening_gaps,
        warnings=warnings,
        data_gaps=data_gaps,
    )
