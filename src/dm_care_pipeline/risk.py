"""
【第4站】風險計算 — 依趨勢報告 + 併發症報告組裝風險因子快照，並計算整體
風險等級。

★★★ 鐵律1 ★★★ `RuleBasedRiskCalculator` 是**架構示意用的簡化實作**，
不是已驗證的臨床風險評分公式（例如 ASCVD Risk Score、UKPDS Risk
Engine）。`RiskCalculator` 定義為 `Protocol`，正式上線前須由臨床端提供
並驗證實際採用之風險評分公式/切點，以新的 `RiskCalculator` 實作替換，
呼叫端只需在 `assess_risk(calculator=...)` 換上新實作即可，不需改動本
模組其餘部分。

HbA1c/LDL 的良好/不良分類**不在本模組重複宣告**，一律讀
`trend_analysis.QualityThresholdConfig` 已算好的 `MarkerTrend.
control_tier`，避免同一組切點在管線中出現第二份實作（見架構文件第7節
裁定#3）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional, Protocol

from .complication_identification import ComplicationReport
from .pipeline_models import PatientClinicalProfile
from .trend_analysis import ClinicalTrendReport, MarkerTrend, QualityMetricTier


class RiskLevel(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    UNKNOWN = "unknown"


_RISK_LEVEL_RANK = {RiskLevel.UNKNOWN: 0, RiskLevel.LOW: 1, RiskLevel.MODERATE: 2, RiskLevel.HIGH: 3}


@dataclass
class RiskCalculatorConfig:
    """★ illustrative placeholder，非已驗證臨床公式（鐵律1）。"""

    bp_placeholder_systolic_high: float = 140.0  # 規格書未提供，純工程佔位
    bp_placeholder_diastolic_high: float = 90.0
    complication_high_risk_categories: frozenset[str] = frozenset({"CVD", "CEREBROVASCULAR", "PVD", "NEPHROPATHY"})
    ckd_high_risk_stages: frozenset[str] = frozenset({"3a"})
    is_placeholder: bool = True  # 恆為 True


@dataclass(frozen=True)
class RiskFactorSnapshot:
    patient_id: str
    as_of_date: date
    hba1c_trend: Optional[MarkerTrend]
    ldl_trend: Optional[MarkerTrend]
    latest_systolic_bp: Optional[float] = None  # TODO：models.py 目前無 BP 資料結構，恆為 None（見架構文件第8節#3）
    latest_diastolic_bp: Optional[float] = None
    complications: frozenset[str] = frozenset()  # COMPLICATION_ICD10_PREFIXES 的 key 集合
    ckd_stage: Optional[str] = None


def build_risk_factor_snapshot(
    profile: PatientClinicalProfile, trend_report: ClinicalTrendReport, complication_report: ComplicationReport
) -> RiskFactorSnapshot:
    """組裝快照，純資料轉換不做判讀。"""
    hba1c_trend = next((mt for mt in trend_report.marker_trends if mt.marker_name == "HBA1C"), None)
    ldl_trend = next((mt for mt in trend_report.marker_trends if mt.marker_name == "LDL"), None)
    complications = frozenset(f.category for f in complication_report.findings)
    ckd_stage = next((f.ckd_stage for f in complication_report.findings if f.category == "NEPHROPATHY" and f.ckd_stage), None)

    return RiskFactorSnapshot(
        patient_id=profile.patient_id,
        as_of_date=profile.as_of_date,
        hba1c_trend=hba1c_trend,
        ldl_trend=ldl_trend,
        latest_systolic_bp=None,
        latest_diastolic_bp=None,
        complications=complications,
        ckd_stage=ckd_stage,
    )


@dataclass(frozen=True)
class RiskFactorContribution:
    factor: str  # "hba1c" / "ldl" / "blood_pressure" / "complication" / "ckd_stage"
    value_summary: str
    level: RiskLevel
    rationale: str  # 需標明依據是 grounded 或 placeholder 門檻


@dataclass
class RiskAssessmentResult:
    patient_id: str
    as_of_date: date
    overall_risk_level: RiskLevel
    contributions: list[RiskFactorContribution]
    methodology_version: str = "illustrative-v0"
    is_placeholder_methodology: bool = True  # 恆 True；下游必須顯示此旗標
    warnings: list[str] = field(default_factory=list)


class RiskCalculator(Protocol):
    def assess(self, snapshot: RiskFactorSnapshot, config: RiskCalculatorConfig) -> RiskAssessmentResult: ...


def _tier_to_level(tier: QualityMetricTier) -> RiskLevel:
    if tier == QualityMetricTier.POOR:
        return RiskLevel.HIGH
    if tier == QualityMetricTier.GOOD:
        return RiskLevel.LOW
    if tier == QualityMetricTier.BORDERLINE:
        return RiskLevel.MODERATE
    return RiskLevel.UNKNOWN


class RuleBasedRiskCalculator:
    """★ 架構示意用簡化實作，非已驗證醫學風險評分公式（鐵律1）。正式上線
    前須由臨床端提供並驗證實際採用之風險評分公式/切點，並以新
    `RiskCalculator` 實作替換。"""

    def assess(self, snapshot: RiskFactorSnapshot, config: RiskCalculatorConfig | None = None) -> RiskAssessmentResult:
        cfg = config or RiskCalculatorConfig()
        contributions: list[RiskFactorContribution] = []
        warnings: list[str] = []

        # --- HbA1c：直接消費 trend_analysis 已分類好的 control_tier ------
        if snapshot.hba1c_trend is not None and snapshot.hba1c_trend.latest_value is not None:
            tier = snapshot.hba1c_trend.control_tier
            contributions.append(
                RiskFactorContribution(
                    factor="hba1c",
                    value_summary=f"latest={snapshot.hba1c_trend.latest_value}, tier={tier.value}",
                    level=_tier_to_level(tier),
                    rationale="grounded: 依 trend_analysis.QualityThresholdConfig 分類"
                    "（該切點本身為 TODO-SPEC-VERIFY，見 trend_analysis.py 模組說明）",
                )
            )
        else:
            contributions.append(
                RiskFactorContribution(
                    factor="hba1c", value_summary="無最新HbA1c資料", level=RiskLevel.UNKNOWN, rationale="無法判斷：查無檢驗值"
                )
            )

        # --- LDL -----------------------------------------------------------
        if snapshot.ldl_trend is not None and snapshot.ldl_trend.latest_value is not None:
            tier = snapshot.ldl_trend.control_tier
            contributions.append(
                RiskFactorContribution(
                    factor="ldl",
                    value_summary=f"latest={snapshot.ldl_trend.latest_value}, tier={tier.value}",
                    level=_tier_to_level(tier),
                    rationale="grounded: 依 trend_analysis.QualityThresholdConfig 分類"
                    "（該切點本身為 TODO-SPEC-VERIFY，見 trend_analysis.py 模組說明）",
                )
            )
        else:
            contributions.append(
                RiskFactorContribution(
                    factor="ldl", value_summary="無最新LDL資料", level=RiskLevel.UNKNOWN, rationale="無法判斷：查無檢驗值"
                )
            )

        # --- 血壓（placeholder，恆為 None，見架構文件第8節#3）-----------------
        if snapshot.latest_systolic_bp is None and snapshot.latest_diastolic_bp is None:
            contributions.append(
                RiskFactorContribution(
                    factor="blood_pressure",
                    value_summary="無血壓資料",
                    level=RiskLevel.UNKNOWN,
                    rationale="placeholder: dm_eligibility.models 目前無血壓資料結構，恆為 None（TODO，需資料整合階段擴充）",
                )
            )
        else:
            is_high = (snapshot.latest_systolic_bp or 0) >= cfg.bp_placeholder_systolic_high or (
                snapshot.latest_diastolic_bp or 0
            ) >= cfg.bp_placeholder_diastolic_high
            contributions.append(
                RiskFactorContribution(
                    factor="blood_pressure",
                    value_summary=f"SBP={snapshot.latest_systolic_bp}, DBP={snapshot.latest_diastolic_bp}",
                    level=RiskLevel.HIGH if is_high else RiskLevel.LOW,
                    rationale=f"placeholder: 純工程佔位切點 SBP>={cfg.bp_placeholder_systolic_high}"
                    f" 或 DBP>={cfg.bp_placeholder_diastolic_high}，非驗證臨床門檻",
                )
            )

        # --- 併發症 ----------------------------------------------------------
        high_risk_hits = snapshot.complications & cfg.complication_high_risk_categories
        if high_risk_hits:
            contributions.append(
                RiskFactorContribution(
                    factor="complication",
                    value_summary=f"命中高風險併發症類別: {sorted(high_risk_hits)}",
                    level=RiskLevel.HIGH,
                    rationale="placeholder: 併發症存在即視為高風險因子之簡化規則，非分級評分公式",
                )
            )
        elif snapshot.complications:
            contributions.append(
                RiskFactorContribution(
                    factor="complication",
                    value_summary=f"命中併發症類別: {sorted(snapshot.complications)}（非高風險分類）",
                    level=RiskLevel.MODERATE,
                    rationale="placeholder: 簡化規則",
                )
            )
        else:
            contributions.append(
                RiskFactorContribution(
                    factor="complication", value_summary="未辨識出併發症", level=RiskLevel.LOW, rationale="grounded: 依 complication_identification 辨識結果"
                )
            )

        # --- CKD 分期 --------------------------------------------------------
        if snapshot.ckd_stage is None:
            contributions.append(
                RiskFactorContribution(
                    factor="ckd_stage", value_summary="無CKD分期資料", level=RiskLevel.UNKNOWN, rationale="無法判斷：查無 CKDAssessment"
                )
            )
        elif snapshot.ckd_stage in cfg.ckd_high_risk_stages:
            contributions.append(
                RiskFactorContribution(
                    factor="ckd_stage",
                    value_summary=f"CKD分期={snapshot.ckd_stage}",
                    level=RiskLevel.HIGH,
                    rationale=f"placeholder: 工程分類 ckd_high_risk_stages={sorted(cfg.ckd_high_risk_stages)}，非驗證臨床風險分級",
                )
            )
        else:
            contributions.append(
                RiskFactorContribution(
                    factor="ckd_stage", value_summary=f"CKD分期={snapshot.ckd_stage}", level=RiskLevel.MODERATE, rationale="placeholder: 工程分類"
                )
            )

        known_levels = [c.level for c in contributions if c.level != RiskLevel.UNKNOWN]
        if not known_levels:
            overall = RiskLevel.UNKNOWN
        else:
            overall = max(known_levels, key=lambda lv: _RISK_LEVEL_RANK[lv])

        return RiskAssessmentResult(
            patient_id=snapshot.patient_id,
            as_of_date=snapshot.as_of_date,
            overall_risk_level=overall,
            contributions=contributions,
            warnings=warnings,
        )


def assess_risk(
    profile: PatientClinicalProfile,
    trend_report: ClinicalTrendReport,
    complication_report: ComplicationReport,
    calculator: RiskCalculator | None = None,
    config: RiskCalculatorConfig | None = None,
) -> RiskAssessmentResult:
    """便利函式：內部呼叫 `build_risk_factor_snapshot()` 後交給
    `calculator.assess()`。"""
    snapshot = build_risk_factor_snapshot(profile, trend_report, complication_report)
    calc = calculator or RuleBasedRiskCalculator()
    return calc.assess(snapshot, config or RiskCalculatorConfig())
