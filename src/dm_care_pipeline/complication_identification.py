"""
【第3站】併發症辨識 — 依 ICD-10-CM 診斷碼辨識糖尿病常見併發症，並提供全
管線併發症分類詞彙的唯一權威來源（`COMPLICATION_ICD10_PREFIXES`）。

碼表對照依據（鐵律2：醫學界廣為接受、穩定的 ICD-10-CM 分類慣例，非
P14/P7 規格書逐字條文，屬規格書未涵蓋範圍但通用醫學編碼知識放行範圍）：
  - E08.2x/E09.2x/E10.2x/E11.2x/E13.2x：糖尿病腎併發症；另納入 N18.x
    （慢性腎臟病分期，沿用 `dm_eligibility.models.EligibilityConfig.
    ckd_primary_diagnosis_icd10_prefixes` 已標記之工程假設）
  - E08.3x/E09.3x/E10.3x/E11.3x/E13.3x：糖尿病眼併發症（含視網膜病變）
  - E08.4x/E09.4x/E10.4x/E11.4x/E13.4x：糖尿病神經併發症
  - E08.5x/E09.5x/E10.5x/E11.5x/E13.5x：糖尿病周邊血管併發症
  - I25.x：缺血性心臟病（廣義心血管疾病之核心碼；I20-I24 為可選擴充，見
    ComplicationConfig.include_broader_ihd_codes）
  - I63.x/I64.x：腦血管疾病/中風

E12（妊娠糖尿病）刻意不納入本碼表前綴組合，因妊娠糖尿病之併發症分期
臨床意義與 E08/E09/E10/E11/E13 不同，規格書 DM_ICD10_PREFIXES 本身也
僅涵蓋 E08-E13 全部六類，此處併發症前綴延續 P14 spec (a) A.1「E08–E13」
之診斷主碼範圍精神，僅排除 E12 是刻意的臨床謹慎選擇——TODO：需臨床端
確認 E12 併發症是否應一併納入。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from .clinical_data_object import ClinicalDomain
from .pipeline_models import PatientClinicalProfile

# ICD-10-CM 對照表（鐵律2）。全管線唯一詞彙表：其餘站點一律使用本 dict 的 key。
COMPLICATION_ICD10_PREFIXES: dict[str, tuple[str, ...]] = {
    "NEPHROPATHY": ("E08.2", "E09.2", "E10.2", "E11.2", "E13.2", "N18"),
    "RETINOPATHY": ("E08.3", "E09.3", "E10.3", "E11.3", "E13.3"),
    "NEUROPATHY": ("E08.4", "E09.4", "E10.4", "E11.4", "E13.4"),
    "PVD": ("E08.5", "E09.5", "E10.5", "E11.5", "E13.5"),
    "CVD": ("I25",),
    "CEREBROVASCULAR": ("I63", "I64"),
    # --- v2 新增（架構文件 3.5 節，鐵律2：ICD-10-CM 通用慣例，非 P14/P7
    #     規格書逐字條文，需臨床端確認）---
    "HEART_FAILURE": ("I50",),
    "MASLD_MASH": ("K76.0", "K75.81"),
    "OBESITY": ("E66",),
    "FOOT_ULCER_HISTORY": ("L97",),
    "AMPUTATION_HISTORY": ("Z89.4", "Z89.5", "Z89.6"),
}

# 併發症類別（COMPLICATION_ICD10_PREFIXES 的 key）→ ClinicalDomain 映射。
# 全管線併發症→domain 映射唯一權威來源（clinical_state.py 只 import 使用，
# 不重複宣告，鐵律7）；不改既有 COMPLICATION_ICD10_PREFIXES 既有 6 類 key
# 語意，只加映射層（架構文件v2 第2節命名統一總表裁定）。
COMPLICATION_CATEGORY_TO_DOMAIN: dict[str, ClinicalDomain] = {
    "NEPHROPATHY": ClinicalDomain.KIDNEY,
    "RETINOPATHY": ClinicalDomain.EYE,
    "NEUROPATHY": ClinicalDomain.NEUROPATHY,
    "PVD": ClinicalDomain.PAD,
    "CVD": ClinicalDomain.ASCVD,
    "CEREBROVASCULAR": ClinicalDomain.CEREBROVASCULAR,
    "HEART_FAILURE": ClinicalDomain.HEART_FAILURE,
    "MASLD_MASH": ClinicalDomain.LIVER,
    "OBESITY": ClinicalDomain.WEIGHT_OBESITY,
    "FOOT_ULCER_HISTORY": ClinicalDomain.FOOT,
    "AMPUTATION_HISTORY": ClinicalDomain.FOOT,
}

# 併發症類別 → 人類可讀病名（供 clinical_state.py 組裝 ClinicalFinding.condition
# 使用，例如規格§30範例 "CKD"/"糖尿病足"）。唯一權威來源，理由同上：類別鍵的
# 擁有者是併發症辨識站，clinical_state.py 只 import 使用（鐵律7）。
# ★ 工程補充：規格書未逐字給出這份對照文字，是否採用中文/英文、精確用詞需
# 臨床端覆核。
COMPLICATION_CATEGORY_DISPLAY_NAME: dict[str, str] = {
    "NEPHROPATHY": "糖尿病腎病變 (Diabetic Nephropathy / CKD)",
    "RETINOPATHY": "糖尿病視網膜病變 (Diabetic Retinopathy)",
    "NEUROPATHY": "糖尿病神經病變 (Diabetic Neuropathy)",
    "PVD": "周邊血管疾病 (Peripheral Vascular Disease)",
    "CVD": "缺血性心臟病 (Ischemic Heart Disease)",
    "CEREBROVASCULAR": "腦血管疾病 (Cerebrovascular Disease)",
    "HEART_FAILURE": "心臟衰竭 (Heart Failure)",
    "MASLD_MASH": "代謝功能障礙相關脂肪肝病 (MASLD/MASH)",
    "OBESITY": "肥胖 (Obesity)",
    "FOOT_ULCER_HISTORY": "糖尿病足潰瘍病史 (Diabetic Foot Ulcer History)",
    "AMPUTATION_HISTORY": "截肢病史 (Amputation History)",
}


@dataclass
class ComplicationConfig:
    code_table: dict[str, tuple[str, ...]] = field(default_factory=lambda: dict(COMPLICATION_ICD10_PREFIXES))
    include_broader_ihd_codes: bool = False  # I20-I24，TODO 待臨床確認是否併入 CVD 類別
    broader_ihd_codes: tuple[str, ...] = ("I20", "I21", "I22", "I23", "I24")
    include_stroke_sequelae_codes: bool = False  # I69.3，TODO 待臨床確認
    stroke_sequelae_codes: tuple[str, ...] = ("I69.3",)
    primary_diagnosis_only: bool = False
    lookback_years: Optional[int] = None  # None=一旦診斷即視為持續存在（不處理已緩解情形，TODO待臨床確認）


@dataclass(frozen=True)
class ComplicationFinding:
    category: str  # COMPLICATION_ICD10_PREFIXES 的 key
    matched_icd10_codes: tuple[str, ...]
    first_diagnosed_date: Optional[date]
    last_diagnosed_date: date
    encounter_ids: tuple[str, ...]
    matched_via_primary_diagnosis: bool
    ckd_stage: Optional[str] = None  # category=="NEPHROPATHY" 時，由 CKDAssessment.stage() 填入


@dataclass
class ComplicationReport:
    patient_id: str
    as_of_date: date
    findings: tuple[ComplicationFinding, ...]  # 僅含「有命中」的類別
    code_table_used: dict[str, tuple[str, ...]]
    warnings: list[str] = field(default_factory=list)


def _effective_code_table(cfg: ComplicationConfig) -> dict[str, tuple[str, ...]]:
    table = {k: tuple(v) for k, v in cfg.code_table.items()}
    if cfg.include_broader_ihd_codes:
        table["CVD"] = table.get("CVD", ()) + cfg.broader_ihd_codes
    if cfg.include_stroke_sequelae_codes:
        table["CEREBROVASCULAR"] = table.get("CEREBROVASCULAR", ()) + cfg.stroke_sequelae_codes
    return table


def _safe_years_before(d: date, years: int) -> date:
    """回傳「d 往前 years 年」的日期。d 為 2/29 且目標年非閏年時落回 2/28，
    而非讓 `date.replace()` 拋出 `ValueError`（鐵律5：邊界日期不得讓管線崩潰）。"""
    try:
        return d.replace(year=d.year - years)
    except ValueError:
        return d.replace(month=2, day=28, year=d.year - years)


def _latest_ckd_stage(profile: PatientClinicalProfile, as_of: date) -> Optional[str]:
    assessments = [a for a in profile.enrollment_state.ckd_assessments if a.assessment_date <= as_of]
    if not assessments:
        return None
    latest = max(assessments, key=lambda a: a.assessment_date)
    return latest.stage()


def identify_complications(profile: PatientClinicalProfile, config: ComplicationConfig | None = None) -> ComplicationReport:
    """對 `profile.enrollment_state.valid_encounters()` 的
    `DiagnosisRecord.icd10_code` 做正規化後之前綴比對（刻意不呼叫既有
    `icd10_prefix3`，因其只回傳前3碼，無法區分 E11.2 與 E11.3）。
    category=="NEPHROPATHY" 時另外查 `profile.enrollment_state.
    ckd_assessments` 填入 `ckd_stage`。"""
    cfg = config or ComplicationConfig()
    code_table = _effective_code_table(cfg)
    state = profile.enrollment_state
    as_of = profile.as_of_date

    # 鐵律5：as_of 是「以此刻評估」的時間錨點，晚於 as_of 的就診紀錄一律不
    # 代表「已知」資訊，不論是否設定 lookback_years 都必須排除。
    encounters = [e for e in state.valid_encounters() if e.visit_date <= as_of]
    if cfg.lookback_years is not None:
        start = _safe_years_before(as_of, cfg.lookback_years)
        encounters = [e for e in encounters if start <= e.visit_date]

    findings: list[ComplicationFinding] = []
    for category, prefixes in code_table.items():
        prefixes_upper = tuple(p.upper() for p in prefixes)
        matched_codes: set[str] = set()
        encounter_ids: set[str] = set()
        first_date: Optional[date] = None
        last_date: Optional[date] = None
        matched_via_primary = False

        for e in encounters:
            for d in e.diagnoses:
                if cfg.primary_diagnosis_only and not d.is_primary:
                    continue
                code_upper = d.icd10_code.upper()
                if any(code_upper.startswith(p) for p in prefixes_upper):
                    matched_codes.add(code_upper)
                    encounter_ids.add(e.encounter_id)
                    if d.is_primary:
                        matched_via_primary = True
                    first_date = e.visit_date if first_date is None else min(first_date, e.visit_date)
                    last_date = e.visit_date if last_date is None else max(last_date, e.visit_date)

        if not matched_codes:
            continue

        ckd_stage = _latest_ckd_stage(profile, as_of) if category == "NEPHROPATHY" else None

        findings.append(
            ComplicationFinding(
                category=category,
                matched_icd10_codes=tuple(sorted(matched_codes)),
                first_diagnosed_date=first_date,
                last_diagnosed_date=last_date,
                encounter_ids=tuple(sorted(encounter_ids)),
                matched_via_primary_diagnosis=matched_via_primary,
                ckd_stage=ckd_stage,
            )
        )

    warnings: list[str] = []
    if not state.encounters:
        warnings.append("查無任何就診紀錄，併發症辨識結果可能不完整")

    findings.sort(key=lambda f: f.category)
    return ComplicationReport(
        patient_id=profile.patient_id,
        as_of_date=as_of,
        findings=tuple(findings),
        code_table_used=code_table,
        warnings=warnings,
    )
