"""
【Layer 2】把 Layer1（`PatientClinicalProfile`）+ Layer3/4/5 既有報告物件
（`ComplicationReport`/`CareGapReport`/`RiskAssessmentResult`/
`CalculatorResult`）合成為單一權威的「病人臨床狀態」物件
`PatientClinicalState`——規格§4/§5 四態安全設計的具體落地。

★★★ 鐵律6 落地 ★★★：`domain_summaries` 對每個 `ClinicalDomain` 都保證有
一筆輸出（哪怕只是 `TrafficLight.GRAY`「尚未介接/未評估」），避免「沒資料
=沒顯示=看起來沒事」；`TrafficLight.GRAY` 只在
`ClinicalDataSourceRegistry` 對應系統為 `NOT_INTEGRATED`（且該 domain 當次
沒有其他來源的 finding）時才出現，優先權高於「查無 finding」。

本檔案是 Layer4 起（Guideline/Medication/Care-Gap/Alert/Education/
Pre-Visit Brief）唯一應該消費的「病人臨床事實來源」——這些站點不應各自重新
從 `ComplicationReport`/`RiskAssessmentResult`/`CalculatorResult` 組裝一份
自己的臨床狀態判斷（架構文件v2 第1節「資料流原則」新增條款）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import TYPE_CHECKING, Mapping, Optional, Protocol

from .calculators import CALCULATOR_ID_TO_DOMAIN
from .calculators.base import CalculatorExecutionStatus, CalculatorResult
from .clinical_data_layer import SourceSystemStatus
from .clinical_data_object import ClinicalDomain, ClinicalFinding, ClinicalStatus, EvidenceItem, SourceSystem
from .complication_identification import COMPLICATION_CATEGORY_DISPLAY_NAME, COMPLICATION_CATEGORY_TO_DOMAIN

if TYPE_CHECKING:  # pragma: no cover - 型別提示用，避免執行期循環 import
    from .care_gap import CareGapItem, CareGapReport
    from .complication_identification import ComplicationReport
    from .pipeline_models import DataGapFlag, PatientClinicalProfile
    from .risk import RiskAssessmentResult


class TrafficLight(str, Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"
    # GRAY 專屬對應 SourceSystemStatus.NOT_INTEGRATED——未介接系統絕不可
    # 顯示綠燈（不可把「沒查」誤讀成「沒事」，鐵律6的核心落地）。
    GRAY = "GRAY"


@dataclass(frozen=True)
class DomainSummary:
    domain: ClinicalDomain
    traffic_light: TrafficLight
    headline: str  # 例如 "CKD G3aA2" / "No DR documented, screened, negative"
    finding_ids: tuple[str, ...] = ()  # 可為空——「確認陰性篩檢」情境見模組 docstring
    last_updated: Optional[date] = None


@dataclass
class PatientClinicalState:
    patient_id: str
    as_of_date: date
    findings: tuple[ClinicalFinding, ...]
    domain_summaries: dict[ClinicalDomain, DomainSummary]
    data_gaps: list["DataGapFlag"]  # 沿用 pipeline_models 既有型別
    warnings: list[str] = field(default_factory=list)

    def confirmed(self) -> tuple[ClinicalFinding, ...]:
        return tuple(f for f in self.findings if f.status == ClinicalStatus.CONFIRMED)

    def suspected(self) -> tuple[ClinicalFinding, ...]:
        return tuple(f for f in self.findings if f.status == ClinicalStatus.SUSPECTED)

    def high_risk(self) -> tuple[ClinicalFinding, ...]:
        return tuple(f for f in self.findings if f.status == ClinicalStatus.HIGH_RISK)

    def care_gaps(self) -> tuple[ClinicalFinding, ...]:
        return tuple(f for f in self.findings if f.status == ClinicalStatus.CARE_GAP)

    def by_domain(self, domain: ClinicalDomain) -> tuple[ClinicalFinding, ...]:
        return tuple(f for f in self.findings if f.domain == domain)

    def get(self, finding_id: str) -> Optional[ClinicalFinding]:
        for f in self.findings:
            if f.finding_id == finding_id:
                return f
        return None


class ClinicalStatusResolver(Protocol):
    """可插拔的 domain 狀態判斷規則（承接 complication_guideline 設計精神，
    輸出型別改為 `ClinicalFinding`，見架構文件v2 第2節命名統一裁定）。
    `derive_clinical_state()` 對 `ClinicalDomain` 逐一呼叫本介面；預設實作
    見 `_DefaultClinicalStatusResolver`。"""

    def resolve(
        self,
        domain: ClinicalDomain,
        profile: "PatientClinicalProfile",
        complication_report: "ComplicationReport",
        care_gap_report: "CareGapReport",
        calculator_results: Mapping[str, CalculatorResult],
    ) -> tuple[ClinicalFinding, ...]: ...


# --- 以下三份對照表為 `_DefaultClinicalStatusResolver` 專用的工程規則化
# 詮釋，非規格書逐字條文，正式上線前需臨床端覆核（比照 v1 EligibilityConfig
# 風格）。---

# CareGapItem.source_codes（LabRequirement.alternatives 的院內醫令代碼）→
# ClinicalDomain。取自 rules_p14.py/rules_p7.py 既有 LabRequirement 定義
# （鐵律7：不重抄檢驗代碼本身，只加一層 domain 分類）。同一代碼若出現在多筆
# LabRequirement，語意需一致，否則需臨床端拆分。
CARE_GAP_LAB_ITEM_TO_DOMAIN: dict[str, ClinicalDomain] = {
    "23501C": ClinicalDomain.EYE,  # 眼底檢查/NMRP
    "23502C": ClinicalDomain.EYE,
    "12111C": ClinicalDomain.KIDNEY,  # 微量白蛋白 ACR/UACR
    "09015C": ClinicalDomain.KIDNEY,  # 血清肌酸酐
    "06013C": ClinicalDomain.KIDNEY,  # 尿液分析
    "09006C": ClinicalDomain.GLYCEMIC_CONTROL,  # HbA1c
    "09139C": ClinicalDomain.GLYCEMIC_CONTROL,  # GA（HbA1c 替代）
    "09005C": ClinicalDomain.GLYCEMIC_CONTROL,  # 空腹血漿葡萄糖/微血管血糖
    "09001C": ClinicalDomain.ASCVD,  # 總膽固醇（血脂四項，供ASCVD風險監測）
    "09004C": ClinicalDomain.ASCVD,  # 三酸甘油脂
    "09043C": ClinicalDomain.ASCVD,  # HDL
    "09044C": ClinicalDomain.ASCVD,  # LDL
    "09026C": ClinicalDomain.LIVER,  # SGPT/ALT（供 FIB-4/MASLD 監測）
}

# ClinicalStatus → TrafficLight。規格§4/§5 未逐字給出四態對應紅黃燈的顏色
# 規則，本檔案裁定：CONFIRMED/HIGH_RISK 較嚴重 → RED；SUSPECTED/CARE_GAP
# 需要行動但尚非確診/高風險定論 → YELLOW。
DEFAULT_STATUS_TO_TRAFFIC_LIGHT: dict[ClinicalStatus, TrafficLight] = {
    ClinicalStatus.CONFIRMED: TrafficLight.RED,
    ClinicalStatus.HIGH_RISK: TrafficLight.RED,
    ClinicalStatus.SUSPECTED: TrafficLight.YELLOW,
    ClinicalStatus.CARE_GAP: TrafficLight.YELLOW,
}

# ClinicalDomain → ClinicalDataSourceRegistry 欄位名，僅列出依賴 Layer1
# 擴充來源系統（`clinical_data_layer.py`）才能評估的 domain；未列出的 domain
# （KIDNEY/ASCVD/CEREBROVASCULAR/GLYCEMIC_CONTROL/HYPOGLYCEMIA）仰賴 dm_eligibility
# 既有 HIS/LIS 資料（`PatientEnrollmentState.encounters`/`lab_results`），
# 不透過本表判斷 GRAY（見 `_domain_traffic_light()`）。
DOMAIN_TO_SOURCE_REGISTRY_FIELD: dict[ClinicalDomain, str] = {
    ClinicalDomain.EYE: "ophthalmology",
    ClinicalDomain.FOOT: "foot_neuro",
    ClinicalDomain.NEUROPATHY: "foot_neuro",
    ClinicalDomain.PAD: "vascular_lab",
    ClinicalDomain.HEART_FAILURE: "cvis",
    ClinicalDomain.LIVER: "lis_extended",
    ClinicalDomain.BLOOD_PRESSURE: "his_vitals",
    ClinicalDomain.WEIGHT_OBESITY: "his_vitals",
}


@dataclass
class ClinicalStateConfig:
    """★ 工程規則化詮釋的具名旗標集合，非規格書逐字給出的判定演算法；
    正式上線前需臨床覆核（比照 v1 EligibilityConfig 風格）。"""

    # open_questions#3：目前預設較保守——單次 eGFR/UACR 異常僅 SUSPECTED，
    # 需臨床端另外提供對應 ICD 診斷佐證才升級為 CONFIRMED。★ 本旗標的實際
    # 生效點在呼叫端組裝 `calculators.ckd_ga.CKDGAInputs.
    # corroborating_ckd_diagnosis` 時（`pipeline.py`，本階段尚未落地）；
    # `derive_clinical_state()` 本身只是原樣採用已算好的
    # `CalculatorResult.clinical_status`（見規格3.3節第3點），不重新判斷，
    # 此欄位目前僅作為文件化承接、暫不在本檔案內產生額外邏輯分支。
    tier_a_confirmed_requires_icd_corroboration: bool = True
    # RuleBasedRiskCalculator 產生的 is_placeholder finding 掛哪個 domain（工程佔位）。
    placeholder_risk_finding_domain: ClinicalDomain = ClinicalDomain.GLYCEMIC_CONTROL
    # --- 以下為本檔案落地時新增的具名旗標（規格pseudocode未列出，但
    # derive_clinical_state() 的確定性行為需要，一併集中管理）---
    status_to_traffic_light: dict[ClinicalStatus, TrafficLight] = field(
        default_factory=lambda: dict(DEFAULT_STATUS_TO_TRAFFIC_LIGHT)
    )
    care_gap_lab_item_to_domain: dict[str, ClinicalDomain] = field(
        default_factory=lambda: dict(CARE_GAP_LAB_ITEM_TO_DOMAIN)
    )
    # Care Gap 項目之 source_codes 皆未登記於 care_gap_lab_item_to_domain 時
    # 的保守 fallback domain（並附警告，不靜默丟棄該筆 care gap）。
    unmapped_care_gap_domain: ClinicalDomain = ClinicalDomain.GLYCEMIC_CONTROL


def _finding_id(used_ids: set[str], domain: ClinicalDomain, slug: str, patient_id: str, as_of: date) -> str:
    """穩定 id 建構（規格建議格式 `f"{domain}:{condition}:{patient_id}:{date}"`），
    同一 run 內若重複（例如同一 domain/slug/date 出現一次以上），附加序號
    避免碰撞——finding_id 需在 Layer5/6 join 時保持唯一（鐵律：不可用
    重複 id 靜默覆蓋另一筆判斷）。"""
    base = f"{domain.value}:{slug}:{patient_id}:{as_of.isoformat()}"
    candidate = base
    n = 2
    while candidate in used_ids:
        candidate = f"{base}#{n}"
        n += 1
    used_ids.add(candidate)
    return candidate


class _DefaultClinicalStatusResolver:
    """`ClinicalStatusResolver` 預設實作：規格3.3節「derive_clinical_state()
    純函式」pseudocode 第1-4點的逐 domain 落地（第5點 risk placeholder 不是
    domain-scoped 輸入，`resolve()` 簽名也未帶 `risk_result`，故留在
    `derive_clinical_state()` 本體另外處理）。"""

    def __init__(self, config: ClinicalStateConfig, used_finding_ids: set[str], generated_at: datetime, warnings: list[str]):
        self._config = config
        self._used_ids = used_finding_ids
        self._generated_at = generated_at
        self._warnings = warnings

    def resolve(
        self,
        domain: ClinicalDomain,
        profile: "PatientClinicalProfile",
        complication_report: "ComplicationReport",
        care_gap_report: "CareGapReport",
        calculator_results: Mapping[str, CalculatorResult],
    ) -> tuple[ClinicalFinding, ...]:
        findings: list[ClinicalFinding] = []
        findings.extend(self._complication_findings(domain, profile, complication_report))
        findings.extend(self._care_gap_findings(domain, profile, care_gap_report))
        findings.extend(self._calculator_findings(domain, profile, calculator_results))
        return tuple(findings)

    def _complication_findings(
        self, domain: ClinicalDomain, profile: "PatientClinicalProfile", complication_report: "ComplicationReport"
    ) -> list[ClinicalFinding]:
        out: list[ClinicalFinding] = []
        for cf in complication_report.findings:
            mapped_domain = COMPLICATION_CATEGORY_TO_DOMAIN.get(cf.category)
            if mapped_domain is None:
                self._warnings.append(
                    f"併發症類別 {cf.category!r} 未登記於 COMPLICATION_CATEGORY_TO_DOMAIN，"
                    "無法歸類 domain，此筆併發症不會出現在任何 domain_summaries"
                )
                continue
            if mapped_domain != domain:
                continue
            condition = COMPLICATION_CATEGORY_DISPLAY_NAME.get(cf.category, cf.category)
            severity = cf.ckd_stage if cf.category == "NEPHROPATHY" else None
            evidence = tuple(
                EvidenceItem(label="ICD-10", value=code, observed_date=cf.last_diagnosed_date, source=SourceSystem.HIS)
                for code in cf.matched_icd10_codes
            )
            out.append(
                ClinicalFinding(
                    finding_id=_finding_id(self._used_ids, domain, cf.category, profile.patient_id, profile.as_of_date),
                    patient_id=profile.patient_id,
                    domain=domain,
                    condition=condition,
                    status=ClinicalStatus.CONFIRMED,
                    severity=severity,
                    evidence=evidence,
                    source=SourceSystem.HIS,
                    date=cf.last_diagnosed_date,
                    generated_at=self._generated_at,
                    is_placeholder=False,
                )
            )
        return out

    def _care_gap_findings(
        self, domain: ClinicalDomain, profile: "PatientClinicalProfile", care_gap_report: "CareGapReport"
    ) -> list[ClinicalFinding]:
        out: list[ClinicalFinding] = []
        for item in care_gap_report.deduplicated_missing_items:
            mapped_domain = self._care_gap_item_domain(item)
            if mapped_domain != domain:
                continue
            evidence: tuple[EvidenceItem, ...] = ()
            if item.most_recent_ever is not None:
                evidence = (
                    EvidenceItem(
                        label="最近一次檢驗（已逾期）",
                        value=str(item.most_recent_ever.value),
                        observed_date=item.most_recent_ever.result_date,
                        source=SourceSystem.LIS,
                    ),
                )
            out.append(
                ClinicalFinding(
                    finding_id=_finding_id(
                        self._used_ids, domain, "CARE_GAP:" + item.requirement.description, profile.patient_id, profile.as_of_date
                    ),
                    patient_id=profile.patient_id,
                    domain=domain,
                    condition=item.requirement.description,
                    status=ClinicalStatus.CARE_GAP,
                    evidence=evidence,
                    source=SourceSystem.DERIVED,
                    date=profile.as_of_date,
                    generated_at=self._generated_at,
                    is_placeholder=False,
                )
            )
        return out

    def _care_gap_item_domain(self, item: "CareGapItem") -> ClinicalDomain:
        for code in item.source_codes:
            mapped = self._config.care_gap_lab_item_to_domain.get(code.upper())
            if mapped is not None:
                return mapped
        self._warnings.append(
            f"CareGapItem(source_codes={item.source_codes!r}) 未登記於 care_gap_lab_item_to_domain，"
            f"已 fallback 至 {self._config.unmapped_care_gap_domain.value}"
        )
        return self._config.unmapped_care_gap_domain

    def _calculator_findings(
        self, domain: ClinicalDomain, profile: "PatientClinicalProfile", calculator_results: Mapping[str, CalculatorResult]
    ) -> list[ClinicalFinding]:
        out: list[ClinicalFinding] = []
        for calculator_id, result in calculator_results.items():
            mapped_domain = CALCULATOR_ID_TO_DOMAIN.get(calculator_id)
            if mapped_domain is None:
                self._warnings.append(
                    f"calculator_id={calculator_id!r} 未登記於 CALCULATOR_ID_TO_DOMAIN，此筆計算結果不會出現在任何 domain_summaries"
                )
                continue
            if mapped_domain != domain:
                continue

            if result.execution_status == CalculatorExecutionStatus.COMPUTED and result.clinical_status is not None:
                evidence = ()
                if result.result_summary:
                    evidence = (EvidenceItem(label=calculator_id, value=result.result_summary, source=SourceSystem.CALCULATOR),)
                # ★ 工程補充：把 result_values["category"]（目前只有
                # IWGDF_FOOT_RISK 有此鍵）帶進 severity，供
                # `care_gap_clocks.IWGDFFootClockRule` 對照
                # `calculators.iwgdf_foot.IWGDF_FOLLOWUP_INTERVAL_DAYS` 使用，
                # 不需另外重跑一次 calculator（鐵律7）。其餘 calculator 的
                # result_values 結構不含此鍵時 severity 維持 None。
                severity = None
                if result.result_values is not None and "category" in result.result_values:
                    severity = str(result.result_values["category"])
                out.append(
                    ClinicalFinding(
                        finding_id=_finding_id(self._used_ids, domain, f"CALC:{calculator_id}", profile.patient_id, profile.as_of_date),
                        patient_id=profile.patient_id,
                        domain=domain,
                        condition=result.result_summary or result.interpretation or calculator_id,
                        status=result.clinical_status,
                        severity=severity,
                        evidence=evidence,
                        source=SourceSystem.CALCULATOR,
                        date=result.computed_at,
                        calculator=calculator_id,
                        calculator_version=result.calculator_version,
                        guideline=result.guideline,
                        is_placeholder=False,
                        generated_at=self._generated_at,
                    )
                )
            elif result.execution_status == CalculatorExecutionStatus.REQUIRES_EXTERNAL_VALIDATED_MODEL:
                # ★ 規格3.3節第4點裁定：Tier B 結果一律轉為 CARE_GAP +
                # is_placeholder=True，condition 文字明確標註「本地驗證前不可
                # 作為風險分級依據」（見架構文件v2 第5節 open_questions#1，
                # 安全優先於畫面還原度）。
                out.append(
                    ClinicalFinding(
                        finding_id=_finding_id(self._used_ids, domain, f"CALC:{calculator_id}", profile.patient_id, profile.as_of_date),
                        patient_id=profile.patient_id,
                        domain=domain,
                        condition=(
                            f"{calculator_id}：需已通過台灣本地驗證/校正之計算服務方可產生風險數值，"
                            "本地驗證前不可作為風險分級依據"
                        ),
                        status=ClinicalStatus.CARE_GAP,
                        severity="pending_local_validation",
                        source=SourceSystem.CALCULATOR,
                        date=result.computed_at,
                        calculator=calculator_id,
                        calculator_version=result.calculator_version,
                        model_provenance=result.model_provenance,
                        is_placeholder=True,
                        generated_at=self._generated_at,
                    )
                )
        return out


def _risk_placeholder_findings(
    profile: "PatientClinicalProfile",
    risk_result: "RiskAssessmentResult",
    config: ClinicalStateConfig,
    used_ids: set[str],
    generated_at: datetime,
) -> list[ClinicalFinding]:
    """規格3.3節第5點：`risk.RuleBasedRiskCalculator` 的 `contributions`
    全部轉成 `is_placeholder=True` 的 finding，`condition` 文字強制帶「非
    已驗證公式」字樣。不是 domain-scoped 輸入（`ClinicalStatusResolver.
    resolve()` 簽名未帶 `risk_result`），統一掛在
    `config.placeholder_risk_finding_domain`。"""
    # ★ status 一律 CARE_GAP（而非依 RiskLevel 挑 HIGH_RISK/SUSPECTED 等）：
    # `ClinicalFinding.status` 是必填欄位（規格§5四態，鐵律3不可為 None），
    # 但 RiskLevel.LOW/MODERATE 兩態在四態安全分級裡都沒有對應語意可用；
    # 這批 contribution 本質上與 Tier B「未經驗證模型」同一類問題——
    # `RuleBasedRiskCalculator.methodology_version="illustrative-v0"`明文
    # 非已驗證方法論，故比照 Tier B 裁定（規格3.3節第4點）統一標記
    # CARE_GAP（『真正驗證過的風險分級』這件事本身尚未完成），RiskLevel
    # 原始值改放 `severity` 供 UI 顯示，不臆造第五種安全等級。
    domain = config.placeholder_risk_finding_domain
    out: list[ClinicalFinding] = []
    for contribution in risk_result.contributions:
        out.append(
            ClinicalFinding(
                finding_id=_finding_id(used_ids, domain, f"RISK:{contribution.factor}", profile.patient_id, profile.as_of_date),
                patient_id=profile.patient_id,
                domain=domain,
                condition=f"{contribution.value_summary}（非已驗證公式，illustrative placeholder，僅供示意）",
                status=ClinicalStatus.CARE_GAP,
                severity=contribution.level.value,
                evidence=(EvidenceItem(label=contribution.factor, value=contribution.rationale, source=SourceSystem.DERIVED),),
                source=SourceSystem.DERIVED,
                date=risk_result.as_of_date,
                is_placeholder=True,
                generated_at=generated_at,
            )
        )
    return out


def _domain_traffic_light(
    domain: ClinicalDomain,
    domain_findings: tuple[ClinicalFinding, ...],
    profile: "PatientClinicalProfile",
    config: ClinicalStateConfig,
) -> TrafficLight:
    """規格3.3節第6點：有 RED/YELLOW finding → 對應燈號；
    `ClinicalDataSourceRegistry` 該 domain 對應系統為 `NOT_INTEGRATED` →
    `GRAY`（優先權高於「查無 finding」）；否則若有
    `INTEGRATED_HAS_DATA` 且無異常 finding → `GREEN`。"""
    lights = {config.status_to_traffic_light.get(f.status) for f in domain_findings if f.status is not None}
    lights.discard(None)
    if TrafficLight.RED in lights:
        return TrafficLight.RED
    if TrafficLight.YELLOW in lights:
        return TrafficLight.YELLOW
    # 無異常 finding：查該 domain 是否依賴 Layer1 擴充來源系統。
    registry_field = DOMAIN_TO_SOURCE_REGISTRY_FIELD.get(domain)
    if registry_field is not None:
        status = getattr(profile.data_source_registry, registry_field)
        if status == SourceSystemStatus.NOT_INTEGRATED:
            return TrafficLight.GRAY
        if status == SourceSystemStatus.INTEGRATED_HAS_DATA:
            return TrafficLight.GREEN
        # INTEGRATED_NO_DATA：★ 保守裁定——「已查詢確認無資料」是「確認陰性」
        # 的必要條件之一，但非充分條件（規格3.1節原文），本檔案在缺乏額外
        # 「已篩檢陰性」證據時，寧可保守回報 GRAY 而非默認 GREEN（鐵律6）。
        return TrafficLight.GRAY
    # 不依賴 Layer1 擴充來源的 domain（仰賴 dm_eligibility 核心 HIS/LIS 資料）：
    # 有任何就診/檢驗紀錄即視為已評估 → GREEN；完全無資料 → GRAY。
    state = profile.enrollment_state
    if state.encounters or state.lab_results:
        return TrafficLight.GREEN
    return TrafficLight.GRAY


def _domain_headline(domain: ClinicalDomain, domain_findings: tuple[ClinicalFinding, ...], light: TrafficLight) -> str:
    if domain_findings:
        return "; ".join(f.condition for f in domain_findings)
    if light == TrafficLight.GRAY:
        return "尚未介接/未評估（No data source integrated for this domain）"
    return "No abnormal finding documented"


def derive_clinical_state(
    profile: "PatientClinicalProfile",
    complication_report: "ComplicationReport",
    care_gap_report: "CareGapReport",
    risk_result: "RiskAssessmentResult",
    calculator_results: Mapping[str, CalculatorResult] | None = None,
    resolver: Optional[ClinicalStatusResolver] = None,
    config: Optional[ClinicalStateConfig] = None,
) -> PatientClinicalState:
    """純函式，reuse 既有 Layer3/4/5 報告物件（不重算）。逐 `ClinicalDomain`
    呼叫 `resolver`（預設 `_DefaultClinicalStatusResolver`，見規格3.3節
    pseudocode 第1-4點），另加 `risk_result` placeholder findings（第5點，
    非 domain-scoped 輸入），最後彙總 `domain_summaries`（第6點）。

    ★ 與規格pseudocode的一處刻意偏離：`calculator_results` 預設值改為
    `None`（內部視為空 dict），而非 pseudocode 寫的 `= ()`——`()` 是
    tuple，不支援 `.items()`，若真的以空 tuple 呼叫本函式會直接 crash，
    此處修正這個 pseudocode 本身的筆誤，行為語意不變（皆代表『沒有計算
    結果』）。
    """
    cfg = config or ClinicalStateConfig()
    calc_results = calculator_results or {}
    generated_at = datetime.now()
    used_ids: set[str] = set()
    warnings: list[str] = list(complication_report.warnings) + list(care_gap_report.warnings) + list(risk_result.warnings)

    active_resolver: ClinicalStatusResolver = resolver or _DefaultClinicalStatusResolver(cfg, used_ids, generated_at, warnings)

    all_findings: list[ClinicalFinding] = []
    domain_summaries: dict[ClinicalDomain, DomainSummary] = {}

    for domain in ClinicalDomain:
        domain_findings = active_resolver.resolve(domain, profile, complication_report, care_gap_report, calc_results)
        if domain == cfg.placeholder_risk_finding_domain:
            domain_findings = domain_findings + tuple(
                _risk_placeholder_findings(profile, risk_result, cfg, used_ids, generated_at)
            )
        all_findings.extend(domain_findings)

        light = _domain_traffic_light(domain, domain_findings, profile, cfg)
        last_updated = max((f.date for f in domain_findings if f.date is not None), default=None)
        domain_summaries[domain] = DomainSummary(
            domain=domain,
            traffic_light=light,
            headline=_domain_headline(domain, domain_findings, light),
            finding_ids=tuple(f.finding_id for f in domain_findings),
            last_updated=last_updated,
        )

    return PatientClinicalState(
        patient_id=profile.patient_id,
        as_of_date=profile.as_of_date,
        findings=tuple(all_findings),
        domain_summaries=domain_summaries,
        data_gaps=list(profile.data_gaps),
        warnings=warnings,
    )
