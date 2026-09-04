"""
【CKD Progression Engine — Component B】OpenClaw HIS §11 Component B：
Longitudinal eGFR / UACR trajectory——不是只顯示單次數值（"eGFR 52"），
而是偵測「連續惡化」趨勢並明確標示（"Progressive decline detected"/
UACR trajectory 上升），比單次 lab 更早辨識 deterioration（§11 原文）。

★★★ 鐵律7 落地 ★★★：方向性判斷/年化斜率/連續惡化次數計算，全部直接
reuse `trend_analysis.py` 既有的 `_direction_from_points()`/
`_linear_slope_per_year()`/`_is_consecutively_worsening()`（與 HbA1c/LDL
用同一套統計方法），本檔案不重寫第二套趨勢演算法，只是資料來源不同：
eGFR/UACR 在本管線是透過 `dm_eligibility.CKDAssessment`（`egfr`/`uacr`
結構化欄位）取得，而非 `trend_analysis.py` 泛用的 `lab_series_by_item`
（LIS item_code 掃描）機制——`trend_analysis.py` 曾經有一個名為 "EGFR"
的 marker 定義，但用的是佔位字串 item_code（規格書未提供 eGFR 專屬醫令
代碼，見該檔案 `_default_markers()` 註解），永遠無法比對到真實資料；
現已改為本檔案透過 `CKDAssessment` 讀取真實數值，`trend_analysis.py`
移除該無效佔位定義。

★ 鐵律1：規格§11 僅給出方向性概念與示意數字（71→65→59→52；
42→89→176→332 mg/g），未給出「連續惡化幾次才算 Progressive」的量化
切點——本檔案沿用 `trend_analysis.ClinicalTrendConfig` 同一組工程保守
預設（`consecutive_worsening_threshold`/`lookback_n`/`min_points_required`），
非規格逐字條文，正式上線前需臨床端確認是否應該有獨立於 HbA1c/LDL 的
切點。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from .pipeline_models import PatientClinicalProfile
from .trend_analysis import (
    ClinicalTrendConfig,
    MarkerTrend,
    QualityMetricTier,
    TrendDirection,
    _direction_from_points,
    _is_consecutively_worsening,
    _linear_slope_per_year,
)

EGFR_MARKER_NAME = "EGFR"
UACR_MARKER_NAME = "UACR"


@dataclass
class CKDProgressionConfig:
    """★ 工程保守預設，非規格逐字條文（見模組 docstring 鐵律1）。刻意與
    `trend_analysis.ClinicalTrendConfig` 預設值一致，確保「多少個點才夠
    判斷趨勢」「連續惡化幾次算 Progressive」這類判準在全管線一致，不會
    HbA1c 用一套標準、eGFR 用另一套卻沒有臨床理由。"""

    method: str = "last_n_compare"
    lookback_n: int = 4
    min_points_required: int = 3
    consecutive_worsening_threshold: int = 2
    lookback_days: Optional[int] = None

    @classmethod
    def from_trend_config(cls, trend_config: ClinicalTrendConfig) -> "CKDProgressionConfig":
        """便利建構子：與呼叫端已有的 `ClinicalTrendConfig`（HbA1c/LDL 用）
        共用同一組取樣參數，避免兩處各自維護一份可能不一致的設定。"""
        return cls(
            method=trend_config.method,
            lookback_n=trend_config.lookback_n,
            min_points_required=trend_config.min_points_required,
            consecutive_worsening_threshold=trend_config.consecutive_worsening_threshold,
            lookback_days=trend_config.lookback_days,
        )


@dataclass
class CKDProgressionReport:
    patient_id: str
    as_of_date: date
    egfr_trend: MarkerTrend
    uacr_trend: MarkerTrend
    warnings: list[str] = field(default_factory=list)


def _points_from_assessments(
    profile: PatientClinicalProfile, field_name: str, cfg: CKDProgressionConfig
) -> list[tuple[date, float]]:
    """依 `field_name`（"egfr" 或 "uacr"）從 `profile.enrollment_state.
    ckd_assessments` 取值，日期升冪排序。鐵律5：晚於 as_of 的評估不代表
    「已知」資訊，一律排除（與 trend_analysis.py／care_gap.py 等既有站點
    同一判準）。"""
    as_of = profile.as_of_date
    points = [
        (a.assessment_date, getattr(a, field_name))
        for a in profile.enrollment_state.ckd_assessments
        if getattr(a, field_name) is not None and a.assessment_date <= as_of
    ]
    points.sort(key=lambda p: p[0])

    if cfg.lookback_days is not None:
        points = [(d, v) for d, v in points if (as_of - d).days <= cfg.lookback_days]

    return points[-cfg.lookback_n :] if cfg.lookback_n > 0 else points


def _build_marker_trend(marker_name: str, used_points: list[tuple[date, float]], higher_is_worse: bool, cfg: CKDProgressionConfig) -> MarkerTrend:
    if len(used_points) < cfg.min_points_required:
        return MarkerTrend(
            marker_name=marker_name,
            item_code_matched=None,  # 資料來自 CKDAssessment 結構化欄位，非 LIS item_code 掃描
            data_points=used_points,
            direction=TrendDirection.INSUFFICIENT_DATA,
            is_consecutively_worsening=False,
            slope_per_year=None,
            latest_value=used_points[-1][1] if used_points else None,
            latest_result_date=used_points[-1][0] if used_points else None,
            control_tier=QualityMetricTier.UNKNOWN,  # 規格書僅對 HbA1c/LDL 定義良好/不良切點
            method_used=cfg.method,
        )

    direction = _direction_from_points(used_points, cfg.method)
    slope = _linear_slope_per_year(used_points)
    worsening = _is_consecutively_worsening(used_points, higher_is_worse, cfg.consecutive_worsening_threshold)
    latest_date, latest_value = used_points[-1]

    return MarkerTrend(
        marker_name=marker_name,
        item_code_matched=None,
        data_points=used_points,
        direction=direction,
        is_consecutively_worsening=worsening,
        slope_per_year=slope,
        latest_value=latest_value,
        latest_result_date=latest_date,
        control_tier=QualityMetricTier.UNKNOWN,
        method_used=cfg.method,
    )


def analyze_ckd_progression(
    profile: PatientClinicalProfile, config: Optional[CKDProgressionConfig] = None
) -> CKDProgressionReport:
    """OpenClaw HIS §11 Component B 進入點。純函式，不做臨床判讀之外的
    副作用；`egfr_trend.is_consecutively_worsening`（eGFR 連續下降，
    `higher_is_worse=False`）與 `uacr_trend.is_consecutively_worsening`
    （UACR 連續上升，`higher_is_worse=True`）供呼叫端（`pre_visit_brief.py`
    Widget 2 3-Year Trend）判斷是否要標示「Progressive decline detected」
    /「UACR trajectory 上升」，本函式本身不產生文字建議（鐵律3：本函式
    只做趨勢判斷，行動建議留給 guideline_recommendation.py 決定）。"""
    cfg = config or CKDProgressionConfig()

    egfr_points = _points_from_assessments(profile, "egfr", cfg)
    uacr_points = _points_from_assessments(profile, "uacr", cfg)

    egfr_trend = _build_marker_trend(EGFR_MARKER_NAME, egfr_points, higher_is_worse=False, cfg=cfg)
    uacr_trend = _build_marker_trend(UACR_MARKER_NAME, uacr_points, higher_is_worse=True, cfg=cfg)

    warnings: list[str] = []
    if not profile.enrollment_state.ckd_assessments:
        warnings.append("查無任何 CKDAssessment 紀錄，無法評估 eGFR/UACR 趨勢")

    return CKDProgressionReport(
        patient_id=profile.patient_id,
        as_of_date=profile.as_of_date,
        egfr_trend=egfr_trend,
        uacr_trend=uacr_trend,
        warnings=warnings,
    )
