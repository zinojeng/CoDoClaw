"""
【第2站】臨床趨勢 — 依歷史檢驗值計算方向性提示，並提供全管線 HbA1c/LDL
良好/不良分類的唯一權威來源（`QualityMetricTier` / `QualityThresholdConfig` /
`classify_quality_metric()`）。

★★★ 重要（docs/臨床決策支援管線設計.md 4.2節 TODO-SPEC-VERIFY）★★★
`QualityThresholdConfig` 的四個切點數值（HbA1c<7.0%良好/>9.0%不良、
LDL<100mg/dl良好/>130mg/dl不良）依任務指示採用「健保署品質獎勵指標」
數值，但逐字覆核 spec/P14_rules_spec.md 全文（關鍵字 7.0/9.0/130/100mg/
HbA1C/品質）並未查到這組切點本身的逐字條文（規格書僅含品項代碼、必要
檢驗清單、180天強制排程、登載不實罰則段落）。正式上線前，須由臨床/
品管端以健保署「品質獎勵指標評核」附件正式函釋核對數值與判定基準
（單次值/連續值/年度平均）。本管線已把這組切點收斂到本模組單一位置，
其餘站點一律消費本模組算好的 `MarkerTrend.control_tier`，不得重新宣告。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Literal, Optional

from .pipeline_models import PatientClinicalProfile


class TrendDirection(str, Enum):
    RISING = "RISING"
    FALLING = "FALLING"
    STABLE = "STABLE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class QualityMetricTier(str, Enum):
    """★ 全管線 HbA1c/LDL 良好/不良分類的唯一權威定義，其餘階段一律消費本
    Enum，不得重新宣告切點或重新分類。"""

    GOOD = "GOOD"
    BORDERLINE = "BORDERLINE"
    POOR = "POOR"
    UNKNOWN = "UNKNOWN"


@dataclass
class QualityThresholdConfig:
    """TODO-SPEC-VERIFY：見本檔案模組層級 docstring。"""

    hba1c_good_upper: float = 7.0
    hba1c_poor_lower: float = 9.0
    ldl_good_upper: float = 100.0
    ldl_poor_lower: float = 130.0


def classify_quality_metric(metric: str, value: Optional[float], config: QualityThresholdConfig) -> QualityMetricTier:
    """純函式。value=None → UNKNOWN。metric 不在 {"HBA1C","LDL"} → UNKNOWN
    （規格書僅對這兩項指定切點，其餘指標無良好/不良二分類）。"""
    if value is None:
        return QualityMetricTier.UNKNOWN
    metric_upper = metric.upper()
    if metric_upper == "HBA1C":
        if value < config.hba1c_good_upper:
            return QualityMetricTier.GOOD
        if value > config.hba1c_poor_lower:
            return QualityMetricTier.POOR
        return QualityMetricTier.BORDERLINE
    if metric_upper == "LDL":
        if value < config.ldl_good_upper:
            return QualityMetricTier.GOOD
        if value > config.ldl_poor_lower:
            return QualityMetricTier.POOR
        return QualityMetricTier.BORDERLINE
    return QualityMetricTier.UNKNOWN


@dataclass(frozen=True)
class TrendMarkerDefinition:
    name: str  # "HBA1C" / "LDL" / "EGFR" / "SBP" / "DBP"
    item_codes: tuple[str, ...]
    higher_is_worse: bool
    good_threshold: Optional[float] = None  # 僅 HbA1c/LDL 有值（供顯示用途，實際判斷仍走 classify_quality_metric）
    bad_threshold: Optional[float] = None


def _default_markers() -> dict[str, TrendMarkerDefinition]:
    return {
        # HBA1C/LDL 的 item_codes 為規格書明文醫令碼（rules_p14.P1407_LAB_REQUIREMENTS_BASE）。
        "HBA1C": TrendMarkerDefinition("HBA1C", ("09006C",), higher_is_worse=True, good_threshold=7.0, bad_threshold=9.0),
        "LDL": TrendMarkerDefinition("LDL", ("09044C",), higher_is_worse=True, good_threshold=100.0, bad_threshold=130.0),
        # ★ 修正（OpenClaw HIS §11 Component B）：先前這裡有一個 "EGFR"
        # marker，item_codes 是佔位字串 "EGFR"（規格書無 eGFR 專屬醫令
        # 代碼可依循），永遠無法比對到 `lab_series_by_item` 的真實資料，
        # 形同一個看起來存在、實際上永遠回傳 INSUFFICIENT_DATA 的死路徑。
        # eGFR/UACR 在本管線是透過 `dm_eligibility.CKDAssessment` 結構化
        # 欄位取得（非泛用 LIS item_code 掃描），改由 `ckd_progression.
        # analyze_ckd_progression()` 使用同一套方向性/斜率演算法（鐵律7）
        # 從 `profile.enrollment_state.ckd_assessments` 讀取真實數值。
        # TODO：血壓（SBP/DBP）同樣是佔位字串，尚未接上
        # `profile.vital_signs`（risk.py 的 `_latest_bp()` 已示範類似做
        # 法），留待後續一併處理。
        "SBP": TrendMarkerDefinition("SBP", ("SBP",), higher_is_worse=True),
        "DBP": TrendMarkerDefinition("DBP", ("DBP",), higher_is_worse=True),
    }


@dataclass
class ClinicalTrendConfig:
    marker_definitions: dict[str, TrendMarkerDefinition] = field(default_factory=_default_markers)
    quality_thresholds: QualityThresholdConfig = field(default_factory=QualityThresholdConfig)
    method: Literal["last_n_compare", "linear_regression"] = "last_n_compare"
    lookback_n: int = 4  # TODO：規格書未定義趨勢判斷之取樣點數，工程保守預設
    min_points_required: int = 3  # TODO：同上
    consecutive_worsening_threshold: int = 2  # TODO：同上
    lookback_days: Optional[int] = None


@dataclass
class MarkerTrend:
    marker_name: str
    item_code_matched: Optional[str]
    data_points: list[tuple[date, float]]
    direction: TrendDirection
    is_consecutively_worsening: bool
    slope_per_year: Optional[float]
    latest_value: Optional[float]
    latest_result_date: Optional[date]
    control_tier: QualityMetricTier  # 僅 HBA1C/LDL 有意義，其餘固定 UNKNOWN
    method_used: str
    note: str = "本結果為依歷史檢驗值計算之方向性提示，非臨床診斷，不可單獨作為醫療決策依據"


@dataclass
class ClinicalTrendReport:
    patient_id: str
    as_of_date: date
    marker_trends: list[MarkerTrend]
    warnings: list[str] = field(default_factory=list)


def _linear_slope_per_year(points: list[tuple[date, float]]) -> Optional[float]:
    """簡化最小平方法：以第一筆日期為基準算日偏移量，回傳年化斜率。
    純工程實作之簡化統計方法，非驗證過的臨床趨勢模型。"""
    if len(points) < 2:
        return None
    base = points[0][0]
    xs = [(d - base).days for d, _ in points]
    ys = [v for _, v in points]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return None
    numer = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope_per_day = numer / denom
    return slope_per_day * 365.0


def _direction_from_points(points: list[tuple[date, float]], method: str) -> TrendDirection:
    if len(points) < 2:
        return TrendDirection.INSUFFICIENT_DATA
    if method == "linear_regression":
        slope = _linear_slope_per_year(points)
        if slope is None:
            return TrendDirection.STABLE
        if slope > 0:
            return TrendDirection.RISING
        if slope < 0:
            return TrendDirection.FALLING
        return TrendDirection.STABLE
    # last_n_compare：比較取樣區間內最早一筆與最新一筆
    first_value = points[0][1]
    last_value = points[-1][1]
    if last_value > first_value:
        return TrendDirection.RISING
    if last_value < first_value:
        return TrendDirection.FALLING
    return TrendDirection.STABLE


def _is_consecutively_worsening(points: list[tuple[date, float]], higher_is_worse: bool, threshold: int) -> bool:
    if len(points) < 2:
        return False
    run = 0
    for i in range(len(points) - 1, 0, -1):
        worse = points[i][1] > points[i - 1][1] if higher_is_worse else points[i][1] < points[i - 1][1]
        if worse:
            run += 1
        else:
            break
    return run >= threshold


def analyze_clinical_trends(profile: PatientClinicalProfile, config: ClinicalTrendConfig | None = None) -> ClinicalTrendReport:
    """優先讀 `profile.lab_series_by_item`（第1站已依 item_code 分組、
    新到舊排序），不重新掃描 `profile.enrollment_state.lab_results`。"""
    cfg = config or ClinicalTrendConfig()
    warnings: list[str] = []
    marker_trends: list[MarkerTrend] = []

    for marker_def in cfg.marker_definitions.values():
        item_code_matched: Optional[str] = None
        series: tuple = ()
        for code in marker_def.item_codes:
            candidate = profile.lab_series_by_item.get(code.upper())
            if candidate:
                item_code_matched = code.upper()
                series = candidate
                break

        # series 是新到舊排序；轉為日期升冪，僅保留有數值者
        points_desc = [(lr.result_date, lr.value) for lr in series if lr.value is not None]
        points_desc.sort(key=lambda p: p[0])  # 升冪

        # 鐵律5：as_of 是「以此刻評估」的時間錨點，晚於 as_of 的檢驗結果一律
        # 不代表「已知」資訊，不論是否設定 lookback_days 都必須排除——否則
        # 未來日期的檢驗值會被當成「最新一筆」納入趨勢判讀。
        cutoff = profile.as_of_date
        points_desc = [(d, v) for d, v in points_desc if d <= cutoff]

        if cfg.lookback_days is not None:
            points_desc = [(d, v) for d, v in points_desc if (cutoff - d).days <= cfg.lookback_days]

        used_points = points_desc[-cfg.lookback_n :] if cfg.lookback_n > 0 else points_desc

        if len(used_points) < cfg.min_points_required:
            marker_trends.append(
                MarkerTrend(
                    marker_name=marker_def.name,
                    item_code_matched=item_code_matched,
                    data_points=used_points,
                    direction=TrendDirection.INSUFFICIENT_DATA,
                    is_consecutively_worsening=False,
                    slope_per_year=None,
                    latest_value=used_points[-1][1] if used_points else None,
                    latest_result_date=used_points[-1][0] if used_points else None,
                    control_tier=classify_quality_metric(
                        marker_def.name, used_points[-1][1] if used_points else None, cfg.quality_thresholds
                    ),
                    method_used=cfg.method,
                )
            )
            continue

        direction = _direction_from_points(used_points, cfg.method)
        slope = _linear_slope_per_year(used_points)
        worsening = _is_consecutively_worsening(used_points, marker_def.higher_is_worse, cfg.consecutive_worsening_threshold)
        latest_date, latest_value = used_points[-1]

        marker_trends.append(
            MarkerTrend(
                marker_name=marker_def.name,
                item_code_matched=item_code_matched,
                data_points=used_points,
                direction=direction,
                is_consecutively_worsening=worsening,
                slope_per_year=slope,
                latest_value=latest_value,
                latest_result_date=latest_date,
                control_tier=classify_quality_metric(marker_def.name, latest_value, cfg.quality_thresholds),
                method_used=cfg.method,
            )
        )

    return ClinicalTrendReport(
        patient_id=profile.patient_id, as_of_date=profile.as_of_date, marker_trends=marker_trends, warnings=warnings
    )
