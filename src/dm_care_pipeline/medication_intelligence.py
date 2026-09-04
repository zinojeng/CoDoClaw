"""
【Medication Intelligence Agent】規格§16「Guideline-Directed Medication
Check」+ §17「Medication Agent 不應直接開藥」的具體落地。

★★★ 鐵律4 落地 ★★★：`build_medication_order_draft()` 只有
`decision.status in (ACCEPTED, MODIFIED)` 時才回傳非 None——用型別系統保證
不存在「PENDING/DECLINED 卻能開藥」的路徑；本檔案不提供任何
`auto_prescribe()`/自動核准之類方法。`MedicationRecommendation` 本身實作
`physician_decision.Reviewable` Protocol（`recommendation_id`/`rule_id`/
`title`/`priority`），可與 `GuidelineRecommendation` 一起流入同一份
`PhysicianDecisionRecord`（見 `physician_decision.py` §3.9 擴充）。

★★★ 鐵律7 落地 ★★★：`assess_ada_level1_hypoglycemia_risk()` 是薄封裝，
實際低血糖風險規則的唯一權威實作仍是
`calculators/hypoglycemia_ada_l1.py::ADAHypoglycemiaLevel1Calculator`，本檔案
不重新宣告 major/minor risk factor 規則。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Callable, Literal, Mapping, Optional, Protocol, Sequence

from .calculators.base import CalculatorExecutionStatus, CalculatorResult
from .calculators.hypoglycemia_ada_l1 import (
    ADAHypoglycemiaLevel1Calculator,
    HypoglycemiaRiskFactorInputs,
)
from .clinical_data_object import ClinicalDomain, ClinicalStatus
from .guideline_recommendation import RecommendationPriority
from .physician_decision import DecisionValidationError, PhysicianDecision, PhysicianDecisionStatus
from .pipeline_models import DataGapFlag, PatientClinicalProfile

if TYPE_CHECKING:  # pragma: no cover - 型別提示用，避免執行期循環 import
    from .clinical_state import PatientClinicalState

# 規格§16逐字示例引用的藥物類別 ATC 前綴對照。鐵律2：WHO ATC 通用醫學編碼
# 慣例；複方製劑（如 SGLT2i+Metformin 複方）ATC 碼可能同時落在兩個前綴，
# 需藥劑部覆核此表是否需要額外拆分規則。
MEDICATION_ATC_CLASS_MAP: dict[str, tuple[str, ...]] = {
    "SGLT2_INHIBITOR": ("A10BK",),
    "GLP1_RA": ("A10BJ",),
    "METFORMIN": ("A10BA",),
    "SULFONYLUREA": ("A10BB",),
    # ★ 修正（Codex #16）：A10BX 是 WHO ATC「其他降血糖藥」子類，不是
    # meglitinide 專屬前綴——guar gum、pramlintide、imeglimin、
    # tirzepatide 等非 meglitinide 藥物也落在 A10BX 下，先前整個前綴都
    # 誤標成 meglitinide。收斂為兩個明確、穩定的 meglitinide 品項代碼
    # （repaglinide A10BX02、nateglinide A10BX03）；完整 A10BX 品項對照
    # 仍需藥劑部逐項覆核（同既有 open_question，不可由本檔案片面決定其餘
    # A10BX 品項是否該歸類為 meglitinide）。
    "MEGLITINIDE": ("A10BX02", "A10BX03"),
    "DPP4_INHIBITOR": ("A10BH",),
    "TZD": ("A10BG",),
    "INSULIN": ("A10A",),
}

# ★ 修正（Codex #16）：A10BD（複方降血糖製劑，如 metformin+SGLT2i 複方）
# 不會匹配上表任何單一前綴，導致病人明明有用藥、卻被 `_map_atc_codes_to_
# drug_classes()` 判定成「未使用任何已知藥物類別」，完全靜默消失——比
# 「查無用藥資料」更危險，因為看起來像已經查過、確認沒用藥。逐一拆解每個
# 複方代碼對應哪些成分需藥劑部持續維護（該對照表變動頻繁），非本檔案可
# 片面決定；改為明確追蹤「有 A10BD 複方但未拆解成分」這件事本身。
COMBINATION_PRODUCT_ATC_PREFIX = "A10BD"


def _map_atc_codes_to_drug_classes(atc_codes: frozenset[str]) -> frozenset[str]:
    classes: set[str] = set()
    for code in atc_codes:
        code_upper = code.upper()
        for drug_class, prefixes in MEDICATION_ATC_CLASS_MAP.items():
            if any(code_upper.startswith(p) for p in prefixes):
                classes.add(drug_class)
    return frozenset(classes)


def has_unclassified_combination_product(atc_codes: frozenset[str]) -> bool:
    """病人是否有 A10BD 複方降血糖製劑，其成分未被 MEDICATION_ATC_CLASS_MAP
    任何單一藥物類別覆蓋。呼叫端可用此旗標觸發「需藥劑部覆核用藥分類」的
    提示，而非誤讀為「未使用任何降血糖藥」（見 COMBINATION_PRODUCT_ATC_PREFIX
    註解）。"""
    return any(c.upper().startswith(COMBINATION_PRODUCT_ATC_PREFIX) for c in atc_codes)


@dataclass(frozen=True)
class MedicationCheckInput:
    patient_id: str
    as_of_date: date
    active_drug_classes: frozenset[str]
    clinical_state: "PatientClinicalState"  # ★ 直接消費 Layer2 輸出，取代各自重讀 ComplicationReport/trend
    kdigo_g_stage: Optional[str]
    kdigo_a_stage: Optional[str]  # 消費 KDIGO_GA CalculatorResult，不重算
    age_years: Optional[int]
    hypoglycemia_level1_result: Optional[CalculatorResult]
    data_gaps: list[DataGapFlag]
    # ★ 本檔案新增（規格pseudocode欄位表未列，但 MedicationReviewPanel.
    # egfr_value 需要）：同樣消費 KDIGO_GA CalculatorResult.result_values
    # ["egfr"]，不重算、不重新讀 lab_results。
    egfr_value: Optional[float] = None


def assess_ada_level1_hypoglycemia_risk(
    profile: PatientClinicalProfile,
    active_drug_classes: frozenset[str],
    *,
    major_factors: frozenset[str] = frozenset(),
    minor_factors: frozenset[str] = frozenset(),
    risk_factors_assessed: bool = False,
    has_medication_data: bool = False,
) -> CalculatorResult:
    """★ 註：本函式在 v2 中是薄封裝——實際邏輯已收斂到
    `calculators/hypoglycemia_ada_l1.py`（唯一權威實作，避免與 Calculator
    Library 重複實作同一規則，鐵律7）。本函式僅把 `active_drug_classes`
    轉譯為 `on_insulin`/`on_sulfonylurea`/`on_meglitinide` 後組裝
    `HypoglycemiaRiskFactorInputs` 交給 calculator；`major_factors`/
    `minor_factors`/`risk_factors_assessed` 預設空/False——呼叫端若未另外
    提供風險因子評估結果，本函式忠實回傳 INSUFFICIENT_DATA（不可靜默假設
    『沒提供=沒風險』，鐵律6），由呼叫端（例如未來 Pre-Visit Brief 組裝層）
    決定是否要另外呼叫並提供完整風險因子。

    ★ 修正（Codex #14）：`active_drug_classes` 為空集合時，先前無條件把
    `on_insulin`/`on_sulfonylurea`/`on_meglitinide` 當成「確認未使用」
    （False），無法區分「完全沒有用藥資料可查」與「查過、確認未使用」。
    `has_medication_data` 預設 False（保守：呼叫端未明確表示有查過用藥
    資料時，視為未知），由呼叫端傳入
    `bool(profile.enrollment_state.encounters)`（與 pipeline.py
    `_build_ada_hypo_inputs()` 同一判準）。"""
    inputs = HypoglycemiaRiskFactorInputs(
        patient_id=profile.patient_id,
        as_of=profile.as_of_date,
        on_insulin=("INSULIN" in active_drug_classes) if has_medication_data else None,
        on_sulfonylurea=("SULFONYLUREA" in active_drug_classes) if has_medication_data else None,
        on_meglitinide=("MEGLITINIDE" in active_drug_classes) if has_medication_data else None,
        major_factors=major_factors,
        minor_factors=minor_factors,
        risk_factors_assessed=risk_factors_assessed,
    )
    return ADAHypoglycemiaLevel1Calculator().compute(inputs)


def build_medication_check_input(
    profile: PatientClinicalProfile,
    clinical_state: "PatientClinicalState",
    calculator_results: Optional[Mapping[str, CalculatorResult]] = None,
) -> MedicationCheckInput:
    calc_results = calculator_results or {}
    active_drug_classes = _map_atc_codes_to_drug_classes(profile.active_medication_atc_codes)

    kdigo_result = calc_results.get("KDIGO_GA")
    kdigo_g_stage: Optional[str] = None
    kdigo_a_stage: Optional[str] = None
    egfr_value: Optional[float] = None
    if (
        kdigo_result is not None
        and kdigo_result.execution_status == CalculatorExecutionStatus.COMPUTED
        and kdigo_result.result_values is not None
    ):
        kdigo_g_stage = kdigo_result.result_values.get("g_stage")
        kdigo_a_stage = kdigo_result.result_values.get("a_stage")
        egfr_value = kdigo_result.result_values.get("egfr")

    hypoglycemia_result = calc_results.get("ADA_HYPO_L1")
    if hypoglycemia_result is None:
        # ★ 零風險因子資訊時的保守預設：呼叫端未提供 major/minor factors，
        # 一律回傳 INSUFFICIENT_DATA（見 assess_ada_level1_hypoglycemia_risk
        # docstring），不臆測風險等級。
        hypoglycemia_result = assess_ada_level1_hypoglycemia_risk(
            profile, active_drug_classes, has_medication_data=bool(profile.enrollment_state.encounters)
        )

    age_years = int(profile.enrollment_state.age_years) if profile.enrollment_state.age_years is not None else None

    data_gaps = list(profile.data_gaps)
    # ★ 修正（Codex #16）：A10BD 複方製劑的成分未被拆解進 active_drug_
    # classes，若不明確回報，這位病人「有用藥、但分類不出成分」會跟「查過
    # 確認沒用藥」看起來一模一樣。
    if has_unclassified_combination_product(profile.active_medication_atc_codes):
        data_gaps.append(
            DataGapFlag(
                source="active_medication_atc_codes",
                status="unknown",
                detail="病人使用 A10BD 複方降血糖製劑，其成分未拆解進 MEDICATION_ATC_CLASS_MAP"
                "（需藥劑部覆核複方成分對照），Guideline-Directed Medication Check 可能低估"
                "實際用藥覆蓋範圍",
                relevant_downstream_stages=("medication_intelligence",),
            )
        )

    return MedicationCheckInput(
        patient_id=profile.patient_id,
        as_of_date=profile.as_of_date,
        active_drug_classes=active_drug_classes,
        clinical_state=clinical_state,
        kdigo_g_stage=kdigo_g_stage,
        kdigo_a_stage=kdigo_a_stage,
        age_years=age_years,
        hypoglycemia_level1_result=hypoglycemia_result,
        data_gaps=data_gaps,
        egfr_value=egfr_value,
    )


@dataclass(frozen=True)
class MedicationCheckEvidence:
    """`MedicationIndicationRule.matcher` 的回傳型別（規格pseudocode只給出
    前向參照 `Optional["MedicationCheckEvidence"]`，本檔案補上完整定義，
    比照 `guideline_recommendation.RecommendationEvidence` 風格）。"""

    detail: str
    related_finding_id: Optional[str] = None


@dataclass(frozen=True)
class MedicationIndicationRule:
    rule_id: str
    guideline_id: str
    title_template: str
    matcher: Callable[[MedicationCheckInput], Optional[MedicationCheckEvidence]]
    priority: RecommendationPriority
    trigger_grounded_in_spec: bool
    action_is_placeholder_content: bool
    spec_reference: str
    # ★ 本檔案新增（規格pseudocode欄位表未列，但落地時需要）：
    candidate_drug_classes: tuple[str, ...] = ()  # 供 ContraindicationChecker 逐一檢查
    recommended_drug_class: Optional[str] = None
    # 若非 None，MedicationRecommendation.recommended_drug_class 帶入此值，
    # build_medication_order_draft() 才可能對此建議產生 order draft；None
    # 代表本建議屬於「檢視既有用藥」而非「新增醫囑」性質（例如
    # HIGH_HYPOGLYCEMIA_RISK_DEINTENSIFICATION），不產生 order draft。


# ---------------------------------------------------------------------------
# 內建三條規則，逐字對應 OpenClaw for Diabetes HIS.md §16 三個範例
# ---------------------------------------------------------------------------


def _has_ckd(inp: MedicationCheckInput) -> bool:
    # ★ 修正（Codex #13）：與 calculators/ckd_ga.py 的 is_normal 判準一致
    # ——KDIGO CKD 定義是「eGFR<60（G3a以下）」或「腎損傷標記（含A2/A3
    # 白蛋白尿）持續≥3個月」二擇一成立，G2（eGFR 60-89）本身不構成 CKD。
    # 先前把「非 G1」都當 CKD，會讓單純 G2A1（無白蛋白尿）的病人被誤判
    # 有 CKD，進而可能被開立不必要的腎臟保護治療缺口建議。
    return (inp.kdigo_g_stage is not None and inp.kdigo_g_stage not in ("G1", "G2")) or (
        inp.kdigo_a_stage is not None and inp.kdigo_a_stage != "A1"
    )


def _kidney_protective_therapy_gap_matcher(inp: MedicationCheckInput) -> Optional[MedicationCheckEvidence]:
    """§16範例一：「CKD G3aA3 + No SGLT2 inhibitor」→「Kidney-protective
    therapy gap detected.」。★ 工程擴充：ADA 2026 原文（緊接在三個範例之後）
    明文「SGLT2 inhibitor 或 GLP-1 RA」都具腎臟保護效益，故本規則檢查兩者
    皆缺席才觸發，而非只檢查 SGLT2 inhibitor 單一藥物類別。"""
    if not _has_ckd(inp):
        return None
    if inp.active_drug_classes & {"SGLT2_INHIBITOR", "GLP1_RA"}:
        return None
    kidney_finding = next(
        (f for f in inp.clinical_state.by_domain(ClinicalDomain.KIDNEY) if f.status == ClinicalStatus.CONFIRMED),
        None,
    )
    return MedicationCheckEvidence(
        detail=(
            f"CKD {inp.kdigo_g_stage or 'G?'}{inp.kdigo_a_stage or 'A?'}，"
            "未使用 SGLT2 inhibitor 或 GLP-1 RA：Kidney-protective therapy gap detected"
        ),
        related_finding_id=kidney_finding.finding_id if kidney_finding else None,
    )


def _secondary_ascvd_prevention_gap_matcher(inp: MedicationCheckInput) -> Optional[MedicationCheckEvidence]:
    """§16範例二：「Previous stroke + LDL 92 mg/dL」→「Secondary ASCVD
    prevention pathway.」。範例本身未點名具體藥物，`recommended_drug_class`
    刻意留 None（見 `default_medication_indication_rules()`），本規則只
    忠實標記「已進入 secondary prevention 情境」，不臆造新藥建議。"""
    ascvd_finding = next(
        (
            f
            for f in inp.clinical_state.findings
            if f.domain in (ClinicalDomain.ASCVD, ClinicalDomain.CEREBROVASCULAR, ClinicalDomain.PAD)
            and f.status == ClinicalStatus.CONFIRMED
        ),
        None,
    )
    if ascvd_finding is None:
        return None
    return MedicationCheckEvidence(
        detail=f"已有 established ASCVD/腦血管/PAD 病史（{ascvd_finding.condition}）：Secondary ASCVD prevention pathway",
        related_finding_id=ascvd_finding.finding_id,
    )


def _high_hypoglycemia_risk_deintensification_matcher(inp: MedicationCheckInput) -> Optional[MedicationCheckEvidence]:
    """§16範例三：「Age 82 + CKD G4 + recurrent hypoglycemia + SU+insulin」
    →「High hypoglycemia risk. Consider treatment deintensification /
    medication review.」。直接消費 `hypoglycemia_level1_result`
    （`calculators/hypoglycemia_ada_l1.py` 唯一權威實作），不重新判斷風險
    因子規則。"""
    result = inp.hypoglycemia_level1_result
    if (
        result is None
        or result.execution_status != CalculatorExecutionStatus.COMPUTED
        or result.clinical_status != ClinicalStatus.HIGH_RISK
    ):
        return None
    risky_meds = inp.active_drug_classes & {"SULFONYLUREA", "INSULIN"}
    if not risky_meds:
        return None
    return MedicationCheckEvidence(
        detail=(
            f"{result.result_summary}；病人使用 {sorted(risky_meds)}："
            "High hypoglycemia risk. Consider treatment deintensification / medication review."
        )
    )


def default_medication_indication_rules() -> list[MedicationIndicationRule]:
    return [
        MedicationIndicationRule(
            rule_id="KIDNEY_PROTECTIVE_THERAPY_GAP",
            guideline_id="ADA_SOC_2026",
            title_template="Kidney-protective therapy gap detected",
            matcher=_kidney_protective_therapy_gap_matcher,
            priority=RecommendationPriority.PRIORITY,
            trigger_grounded_in_spec=True,
            action_is_placeholder_content=False,
            spec_reference="OpenClaw for Diabetes HIS.md §16",
            candidate_drug_classes=("SGLT2_INHIBITOR", "GLP1_RA"),
            recommended_drug_class="SGLT2_INHIBITOR",
        ),
        MedicationIndicationRule(
            rule_id="SECONDARY_ASCVD_PREVENTION_GAP",
            guideline_id="ADA_SOC_2026",
            title_template="Secondary ASCVD prevention pathway",
            matcher=_secondary_ascvd_prevention_gap_matcher,
            priority=RecommendationPriority.PRIORITY,
            trigger_grounded_in_spec=True,
            action_is_placeholder_content=False,
            spec_reference="OpenClaw for Diabetes HIS.md §16",
            candidate_drug_classes=("SGLT2_INHIBITOR", "GLP1_RA"),
            recommended_drug_class=None,  # 範例未點名具體藥物，不臆造
        ),
        MedicationIndicationRule(
            rule_id="HIGH_HYPOGLYCEMIA_RISK_DEINTENSIFICATION",
            guideline_id="ADA_SOC_2026",
            title_template="High hypoglycemia risk. Consider treatment deintensification / medication review.",
            matcher=_high_hypoglycemia_risk_deintensification_matcher,
            priority=RecommendationPriority.URGENT,
            trigger_grounded_in_spec=True,
            action_is_placeholder_content=False,
            spec_reference="OpenClaw for Diabetes HIS.md §16, §8",
            candidate_drug_classes=("SULFONYLUREA", "INSULIN"),
            recommended_drug_class=None,  # 檢視既有用藥，非新增醫囑
        ),
    ]


# ---------------------------------------------------------------------------
# ContraindicationChecker
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContraindicationFlag:
    drug_class: str
    status: Literal["not_evaluated", "contraindicated", "caution", "no_contraindication_found"]
    detail: str = ""


class ContraindicationChecker(Protocol):
    def check(self, inp: MedicationCheckInput, drug_class: str) -> tuple[ContraindicationFlag, ...]: ...


class NullContraindicationChecker:
    """預設實作：全部回傳 `status="not_evaluated"`，避免醫師誤以為系統已
    排除禁忌症（呼應 Tier B 的 `execution_status` 精神——『沒查』與『查了
    確認沒事』是兩件事）。"""

    def check(self, inp: MedicationCheckInput, drug_class: str) -> tuple[ContraindicationFlag, ...]:
        return (
            ContraindicationFlag(
                drug_class=drug_class,
                status="not_evaluated",
                detail="尚未串接禁忌症判斷邏輯（例如 eGFR 下限、藥物交互作用），不可視為『已排除禁忌症』",
            ),
        )


@dataclass(frozen=True)
class MedicationReviewPanel:
    indication: str
    egfr_value: Optional[float]
    egfr_data_gap: bool
    current_medications: tuple[str, ...]
    contraindications: tuple[ContraindicationFlag, ...]
    guideline_source: str
    guideline_section_or_spec_reference: str


@dataclass(frozen=True)
class MedicationRecommendation:
    """實作 `physician_decision.Reviewable` Protocol
    （`recommendation_id`/`rule_id`/`title`/`priority`）。"""

    recommendation_id: str
    rule_id: str
    title: str
    priority: RecommendationPriority
    related_finding_id: Optional[str]  # 對齊 guideline_recommendation.py 的 finding_id 外鍵慣例
    review_panel: MedicationReviewPanel
    recommended_drug_class: Optional[str] = None  # 見 MedicationIndicationRule 說明


@dataclass
class MedicationIntelligenceReport:
    patient_id: str
    as_of_date: date
    recommendations: list[MedicationRecommendation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def build_medication_intelligence_report(
    inp: MedicationCheckInput,
    rules: Optional[Sequence[MedicationIndicationRule]] = None,
    contraindication_checker: Optional[ContraindicationChecker] = None,
) -> MedicationIntelligenceReport:
    active_rules = list(rules) if rules is not None else default_medication_indication_rules()
    checker = contraindication_checker or NullContraindicationChecker()
    recommendations: list[MedicationRecommendation] = []
    # ★ 修正（Codex #16）：`inp.data_gaps`（含 build_medication_check_input()
    # 新增的「A10BD 複方成分未拆解」缺漏）先前完全沒有被本函式讀取，等於
    # 有記錄卻無人看見。只帶入標記給本站（"medication_intelligence"）的
    # 缺漏，不重複帶入其他站點專屬的缺漏（鐵律5：資料缺漏不可靜默，需外顯）。
    warnings: list[str] = [
        gap.detail for gap in inp.data_gaps if "medication_intelligence" in gap.relevant_downstream_stages
    ]

    for rule in active_rules:
        try:
            evidence = rule.matcher(inp)
        except Exception as exc:  # matcher 拋例外時捕捉、記入 warnings、跳過該規則，不中斷整體流程
            warnings.append(f"規則 {rule.rule_id} 執行失敗，已略過：{exc!r}")
            continue
        if evidence is None:
            continue

        contraindications: list[ContraindicationFlag] = []
        for drug_class in rule.candidate_drug_classes:
            try:
                contraindications.extend(checker.check(inp, drug_class))
            except Exception as exc:
                warnings.append(
                    f"規則 {rule.rule_id} 的 contraindication_checker 對 {drug_class!r} 執行失敗，已略過：{exc!r}"
                )

        review_panel = MedicationReviewPanel(
            indication=evidence.detail,
            egfr_value=inp.egfr_value,
            egfr_data_gap=inp.egfr_value is None,
            current_medications=tuple(sorted(inp.active_drug_classes)),
            contraindications=tuple(contraindications),
            guideline_source=rule.guideline_id,
            guideline_section_or_spec_reference=rule.spec_reference,
        )
        recommendation_id = f"{rule.rule_id}::{inp.patient_id}::{inp.as_of_date.isoformat()}"
        recommendations.append(
            MedicationRecommendation(
                recommendation_id=recommendation_id,
                rule_id=rule.rule_id,
                title=rule.title_template,
                priority=rule.priority,
                related_finding_id=evidence.related_finding_id,
                review_panel=review_panel,
                recommended_drug_class=rule.recommended_drug_class,
            )
        )

    return MedicationIntelligenceReport(
        patient_id=inp.patient_id, as_of_date=inp.as_of_date, recommendations=recommendations, warnings=warnings
    )


# ---------------------------------------------------------------------------
# §17：Medication Agent 不應直接開藥
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MedicationOrderDraft:
    """只到藥物 class 層級（例如 "SGLT2_INHIBITOR"），不含特定藥品/劑量
    ——實際品項/劑量由醫師於 HIS 選擇（規格§17「Open HIS medication
    order」之後仍需醫師確認才真正送出）。"""

    recommendation_id: str
    drug_class: str
    order_text: str
    physician_decision_status: str
    physician_id: Optional[str] = None


def build_medication_order_draft(
    recommendation: MedicationRecommendation,
    decision: PhysicianDecision,
    review_panel: MedicationReviewPanel,
) -> Optional[MedicationOrderDraft]:
    """★ 只有 `decision.status in (ACCEPTED, MODIFIED)` 時才回傳非
    None——用型別系統保證不存在『PENDING/DECLINED 卻能開藥』的路徑（鐵律4）。
    `recommendation.recommended_drug_class is None`（例如
    SECONDARY_ASCVD_PREVENTION_GAP/HIGH_HYPOGLYCEMIA_RISK_DEINTENSIFICATION
    這類「檢視既有用藥」而非「新增醫囑」的建議）時同樣回傳 None——不存在
    對應的新藥可開立。

    ★ 修正（Codex 審閱發現）：`decision`/`recommendation` 是呼叫端分別傳入
    的兩個獨立參數，本函式先前未驗證兩者其實對應同一筆建議——若呼叫端
    傳錯（例如把 A 建議的 ACCEPTED 決定誤配給 B 建議），會產生一份看似
    合法、實則授權錯誤藥物 class 的醫令草稿。這是呼叫端邏輯錯誤而非合法
    業務狀態，故 raise 而非靜默回傳 None（鐵律4：任何授權路徑上的不一致
    都必須顯式失敗，不可悄悄放行）。"""
    if decision.recommendation_id != recommendation.recommendation_id:
        raise DecisionValidationError(
            f"build_medication_order_draft(): decision.recommendation_id={decision.recommendation_id!r} "
            f"與 recommendation.recommendation_id={recommendation.recommendation_id!r} 不一致，"
            "疑似呼叫端傳入不對應的 (recommendation, decision) 配對，拒絕產生醫令草稿"
        )
    if decision.status not in (PhysicianDecisionStatus.ACCEPTED, PhysicianDecisionStatus.MODIFIED):
        return None
    if recommendation.recommended_drug_class is None:
        return None

    if decision.status == PhysicianDecisionStatus.MODIFIED and decision.modified_action_text:
        order_text = decision.modified_action_text
    else:
        order_text = (
            f"開立 {recommendation.recommended_drug_class} 類藥物"
            "（僅藥物 class 層級，實際品項/劑量由醫師於 HIS 選擇）"
        )

    return MedicationOrderDraft(
        recommendation_id=recommendation.recommendation_id,
        drug_class=recommendation.recommended_drug_class,
        order_text=order_text,
        physician_decision_status=decision.status.value,
        physician_id=decision.physician_id,
    )
