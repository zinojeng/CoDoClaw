"""
`ckd_progression.py`（OpenClaw HIS §11 Component B — Longitudinal eGFR/
UACR trajectory）測試。

涵蓋情境：
- eGFR 連續下降 → FALLING + is_consecutively_worsening（"Progressive
  decline detected" 的資料基礎）
- UACR 連續上升 → RISING + is_consecutively_worsening
- 點數不足 → INSUFFICIENT_DATA（不臆測趨勢）
- 未來日期的 CKDAssessment 不納入（鐵律5）
- 完全無 CKDAssessment → 兩個 trend 皆 INSUFFICIENT_DATA + 顯式 warning
- 與 `trend_analysis.py` 共用同一組取樣參數（`from_trend_config()`）
"""

from __future__ import annotations

from datetime import date, timedelta

from dm_eligibility.models import CKDAssessment, PatientEnrollmentState

from dm_care_pipeline.ckd_progression import CKDProgressionConfig, analyze_ckd_progression
from dm_care_pipeline.data_integration import build_patient_clinical_profile
from dm_care_pipeline.trend_analysis import ClinicalTrendConfig, TrendDirection

AS_OF = date(2024, 6, 1)


def make_profile(ckd_assessments):
    state = PatientEnrollmentState(patient_id="P1", as_of_date=AS_OF, ckd_assessments=ckd_assessments)
    return build_patient_clinical_profile(state)


def test_egfr_progressive_decline_detected():
    """回歸的正向情境（OpenClaw HIS §11 範例：71→65→59→52）：連續下降
    應標記 FALLING + is_consecutively_worsening，供呼叫端顯示
    "Progressive decline detected"。"""
    profile = make_profile(
        [
            CKDAssessment(AS_OF - timedelta(days=270), egfr=71.0, uacr=42.0, is_diabetic=True),
            CKDAssessment(AS_OF - timedelta(days=180), egfr=65.0, uacr=89.0, is_diabetic=True),
            CKDAssessment(AS_OF - timedelta(days=90), egfr=59.0, uacr=176.0, is_diabetic=True),
            CKDAssessment(AS_OF, egfr=52.0, uacr=332.0, is_diabetic=True),
        ]
    )
    report = analyze_ckd_progression(profile)

    assert report.egfr_trend.direction == TrendDirection.FALLING
    assert report.egfr_trend.is_consecutively_worsening is True
    assert report.egfr_trend.latest_value == 52.0
    assert report.egfr_trend.slope_per_year is not None and report.egfr_trend.slope_per_year < 0


def test_uacr_increasing_trajectory_detected():
    """OpenClaw HIS §11 範例：42→89→176→332 mg/g，UACR 越高越差。"""
    profile = make_profile(
        [
            CKDAssessment(AS_OF - timedelta(days=270), egfr=71.0, uacr=42.0, is_diabetic=True),
            CKDAssessment(AS_OF - timedelta(days=180), egfr=65.0, uacr=89.0, is_diabetic=True),
            CKDAssessment(AS_OF - timedelta(days=90), egfr=59.0, uacr=176.0, is_diabetic=True),
            CKDAssessment(AS_OF, egfr=52.0, uacr=332.0, is_diabetic=True),
        ]
    )
    report = analyze_ckd_progression(profile)

    assert report.uacr_trend.direction == TrendDirection.RISING
    assert report.uacr_trend.is_consecutively_worsening is True
    assert report.uacr_trend.latest_value == 332.0
    assert report.uacr_trend.slope_per_year is not None and report.uacr_trend.slope_per_year > 0


def test_stable_egfr_not_flagged_as_worsening():
    """正向對照：eGFR 沒有連續下降時不應標記 is_consecutively_worsening。"""
    profile = make_profile(
        [
            CKDAssessment(AS_OF - timedelta(days=270), egfr=70.0, uacr=20.0, is_diabetic=True),
            CKDAssessment(AS_OF - timedelta(days=180), egfr=72.0, uacr=18.0, is_diabetic=True),
            CKDAssessment(AS_OF - timedelta(days=90), egfr=69.0, uacr=22.0, is_diabetic=True),
            CKDAssessment(AS_OF, egfr=71.0, uacr=19.0, is_diabetic=True),
        ]
    )
    report = analyze_ckd_progression(profile)

    assert report.egfr_trend.is_consecutively_worsening is False
    assert report.uacr_trend.is_consecutively_worsening is False


def test_insufficient_points_is_insufficient_data_not_fabricated_trend():
    profile = make_profile([CKDAssessment(AS_OF, egfr=52.0, uacr=332.0, is_diabetic=True)])
    report = analyze_ckd_progression(profile)

    assert report.egfr_trend.direction == TrendDirection.INSUFFICIENT_DATA
    assert report.uacr_trend.direction == TrendDirection.INSUFFICIENT_DATA
    # 唯一一筆仍應顯示「目前最新值」供 Widget 1 使用，不因趨勢不足而整個消失
    assert report.egfr_trend.latest_value == 52.0
    assert report.uacr_trend.latest_value == 332.0


def test_future_dated_assessment_ignored():
    """鐵律5：晚於 as_of 的評估不代表「已知」資訊，與 complication_
    identification.py／care_gap.py 等既有站點同一判準。"""
    profile = make_profile(
        [
            CKDAssessment(AS_OF - timedelta(days=90), egfr=59.0, uacr=176.0, is_diabetic=True),
            CKDAssessment(AS_OF - timedelta(days=180), egfr=65.0, uacr=89.0, is_diabetic=True),
            CKDAssessment(AS_OF - timedelta(days=270), egfr=71.0, uacr=42.0, is_diabetic=True),
            CKDAssessment(AS_OF + timedelta(days=90), egfr=10.0, uacr=900.0, is_diabetic=True),  # 未來，不應採用
        ]
    )
    report = analyze_ckd_progression(profile)

    assert report.egfr_trend.latest_value == 59.0
    assert report.uacr_trend.latest_value == 176.0


def test_no_ckd_assessments_yields_insufficient_data_and_warning():
    profile = make_profile([])
    report = analyze_ckd_progression(profile)

    assert report.egfr_trend.direction == TrendDirection.INSUFFICIENT_DATA
    assert report.uacr_trend.direction == TrendDirection.INSUFFICIENT_DATA
    assert report.warnings  # 顯式標記查無資料，不可靜默略過（鐵律5）


def test_from_trend_config_shares_sampling_parameters():
    """與 trend_analysis.py 共用同一組取樣參數（不各自維護一份可能不一致
    的設定）。"""
    trend_cfg = ClinicalTrendConfig(lookback_n=6, min_points_required=2, consecutive_worsening_threshold=3)
    ckd_cfg = CKDProgressionConfig.from_trend_config(trend_cfg)

    assert ckd_cfg.lookback_n == 6
    assert ckd_cfg.min_points_required == 2
    assert ckd_cfg.consecutive_worsening_threshold == 3
    assert ckd_cfg.method == trend_cfg.method
