"""
dm_care_pipeline（臨床決策支援管線）測試。

涵蓋情境（依任務要求）：
- 資料整合正確彙整多來源資料（就診/檢驗/用藥/CKD評估）
- 趨勢判斷方向正確（上升/下降）
- 併發症辨識正確命中 ICD-10 碼表分類、且不誤判無關診斷
- Care Gap 抓出缺漏項目（且已滿足項目不誤報）
- Guideline Recommendation 的建議有明確依據欄位（evidence/spec_reference）
- 醫師決策紀錄結構（採納/修改/婉拒 + 驗證錯誤）
- 病人衛教對照表選中正確主題（併發症驅動 + 醫師核可建議驅動）
- 後續追蹤日期計算正確
- 額外：風險計算整體等級、pipeline.py 端到端串接
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from dm_eligibility.models import (
    CKDAssessment,
    CodeClaim,
    DiagnosisRecord,
    Encounter,
    LabResult,
    MedicationOrder,
    PatientEnrollmentState,
    PhysicianStatus,
)
from dm_eligibility.engine import EligibilityEngine

from dm_care_pipeline.data_integration import build_patient_clinical_profile
from dm_care_pipeline.trend_analysis import (
    ClinicalTrendConfig,
    TrendDirection,
    analyze_clinical_trends,
)
from dm_care_pipeline.complication_identification import ComplicationConfig, identify_complications
from dm_care_pipeline.risk import RiskLevel, assess_risk
from dm_care_pipeline.care_gap import _QUALITY_MONITORING_PSEUDO_CODE, assess_care_gaps
from dm_care_pipeline.guideline_recommendation import (
    GuidelineRecommendationEngine,
    build_guideline_input,
)
from dm_care_pipeline.physician_decision import (
    DecisionValidationError,
    PhysicianDecision,
    PhysicianDecisionStatus,
    present_for_decision,
)
from dm_care_pipeline.education import EducationTopicMappingConfig, select_education_topics
from dm_care_pipeline.followup import SUBSEQUENT_TRACKING_INTERVAL_DAYS, compute_follow_up_plan
from dm_care_pipeline.pipeline import finalize_pipeline, run_stages_1_to_7


AS_OF = date(2024, 6, 1)


# ---------------------------------------------------------------------------
# 測試輔助函式
# ---------------------------------------------------------------------------


def dm_encounter(visit_date: date, icd10: str = "E11.21", with_med: bool = True) -> Encounter:
    return Encounter(
        encounter_id=f"E-{visit_date.isoformat()}",
        visit_date=visit_date,
        physician_id="DOC1",
        diagnoses=(DiagnosisRecord(icd10, is_primary=True),),
        medication_orders=(MedicationOrder("A10BA02"),) if with_med else (),
    )


def make_state(**overrides) -> PatientEnrollmentState:
    defaults = dict(patient_id="P1", as_of_date=AS_OF)
    defaults.update(overrides)
    return PatientEnrollmentState(**defaults)


# ---------------------------------------------------------------------------
# 1. 資料整合
# ---------------------------------------------------------------------------


def test_data_integration_aggregates_multiple_sources():
    state = make_state(
        encounters=[
            dm_encounter(AS_OF - timedelta(days=100)),
            dm_encounter(AS_OF),
        ],
        lab_results=[
            LabResult("09006C", AS_OF - timedelta(days=10), value=8.0),
            LabResult("09044C", AS_OF - timedelta(days=5), value=120.0),
        ],
        age_years=60,
    )
    profile = build_patient_clinical_profile(state)

    # 檢驗結果依 item_code 分組，且組內新到舊排序
    assert set(profile.lab_series_by_item.keys()) == {"09006C", "09044C"}
    assert profile.lab_series_by_item["09006C"][0].value == 8.0

    # 診斷/用藥彙整
    assert "E11.21" in profile.active_diagnosis_codes
    assert "A10BA02" in profile.active_medication_atc_codes

    # composition：profile.enrollment_state 直接持有同一份物件
    assert profile.enrollment_state is state

    # 沒有 eligibility_report 時應顯式標記 data_gap，而非靜默略過
    gap_sources = {g.source for g in profile.data_gaps}
    assert "eligibility_report" in gap_sources


def test_data_integration_flags_missing_age_and_physician():
    state = make_state(encounters=[], lab_results=[], age_years=None)
    profile = build_patient_clinical_profile(state, physician=None)

    gap_sources = {g.source for g in profile.data_gaps}
    assert "age_years" in gap_sources
    assert "physician" in gap_sources
    assert "encounters" in gap_sources
    assert "lab_results" in gap_sources


# ---------------------------------------------------------------------------
# 2. 臨床趨勢
# ---------------------------------------------------------------------------


def test_trend_direction_rising_for_worsening_hba1c():
    state = make_state(
        lab_results=[
            LabResult("09006C", AS_OF - timedelta(days=90), value=6.5),
            LabResult("09006C", AS_OF - timedelta(days=60), value=7.5),
            LabResult("09006C", AS_OF - timedelta(days=30), value=9.5),
        ]
    )
    profile = build_patient_clinical_profile(state)
    report = analyze_clinical_trends(profile)

    hba1c = next(mt for mt in report.marker_trends if mt.marker_name == "HBA1C")
    assert hba1c.direction == TrendDirection.RISING
    assert hba1c.is_consecutively_worsening is True
    assert hba1c.latest_value == 9.5


def test_trend_direction_falling_for_improving_ldl():
    state = make_state(
        lab_results=[
            LabResult("09044C", AS_OF - timedelta(days=90), value=160.0),
            LabResult("09044C", AS_OF - timedelta(days=60), value=140.0),
            LabResult("09044C", AS_OF - timedelta(days=30), value=90.0),
        ]
    )
    profile = build_patient_clinical_profile(state)
    report = analyze_clinical_trends(profile, ClinicalTrendConfig(method="last_n_compare"))

    ldl = next(mt for mt in report.marker_trends if mt.marker_name == "LDL")
    assert ldl.direction == TrendDirection.FALLING
    # 最新值 90 < good_upper(100)，應分類為 GOOD（trend_analysis 唯一權威來源）
    from dm_care_pipeline.trend_analysis import QualityMetricTier

    assert ldl.control_tier == QualityMetricTier.GOOD


def test_trend_insufficient_data_when_below_min_points():
    state = make_state(lab_results=[LabResult("09006C", AS_OF - timedelta(days=10), value=8.0)])
    profile = build_patient_clinical_profile(state)
    report = analyze_clinical_trends(profile)
    hba1c = next(mt for mt in report.marker_trends if mt.marker_name == "HBA1C")
    assert hba1c.direction == TrendDirection.INSUFFICIENT_DATA


def test_trend_ignores_future_dated_lab_results():
    """回歸測試（Codex #7）：晚於 as_of 的檢驗結果先前未被排除，會被當成
    「最新一筆」納入趨勢判讀——即使 lookback_days 有設定，負的天數差
    （未來日期）仍會通過 `<= lookback` 比較。"""
    state = make_state(
        lab_results=[
            LabResult("09006C", AS_OF - timedelta(days=90), value=6.5),
            LabResult("09006C", AS_OF - timedelta(days=60), value=7.5),
            LabResult("09006C", AS_OF - timedelta(days=30), value=9.5),
            LabResult("09006C", AS_OF + timedelta(days=10), value=12.0),  # 未來日期，不應被採用
        ]
    )
    profile = build_patient_clinical_profile(state)
    report = analyze_clinical_trends(profile, ClinicalTrendConfig(lookback_days=365))

    hba1c = next(mt for mt in report.marker_trends if mt.marker_name == "HBA1C")
    assert hba1c.latest_value == 9.5
    assert hba1c.latest_result_date == AS_OF - timedelta(days=30)


# ---------------------------------------------------------------------------
# 3. 併發症辨識
# ---------------------------------------------------------------------------


def test_complication_identification_matches_expected_categories():
    state = make_state(
        encounters=[
            dm_encounter(AS_OF - timedelta(days=30), icd10="E11.21"),  # NEPHROPATHY
            dm_encounter(AS_OF - timedelta(days=20), icd10="E11.311"),  # RETINOPATHY
            dm_encounter(AS_OF - timedelta(days=10), icd10="E11.9"),  # 無併發症（單純DM，不應命中任何類別）
        ],
        ckd_assessments=[CKDAssessment(AS_OF - timedelta(days=10), egfr=50.0, upcr=200.0, is_diabetic=True)],
    )
    profile = build_patient_clinical_profile(state)
    report = identify_complications(profile)

    categories = {f.category for f in report.findings}
    assert categories == {"NEPHROPATHY", "RETINOPATHY"}

    nephropathy = next(f for f in report.findings if f.category == "NEPHROPATHY")
    assert "E11.21" in nephropathy.matched_icd10_codes
    assert nephropathy.ckd_stage == "3a"  # CKDAssessment.stage() 重用，egfr 45-59.9 → "3a"

    retinopathy = next(f for f in report.findings if f.category == "RETINOPATHY")
    assert retinopathy.ckd_stage is None  # 只有 NEPHROPATHY 才填 ckd_stage


def test_complication_identification_no_false_positive_without_diagnosis():
    state = make_state(encounters=[dm_encounter(AS_OF, icd10="E11.9")])
    profile = build_patient_clinical_profile(state)
    report = identify_complications(profile)
    assert report.findings == ()


def test_complication_identification_ignores_future_dated_encounter():
    """回歸測試（Codex #8）：晚於 as_of 的就診紀錄先前未被排除——即使沒有
    設定 lookback_years，encounters 也從未被限制在 <= as_of，導致未來日期
    的診斷產生「目前存在」的併發症判定，且判定日期落在未來。"""
    state = make_state(encounters=[dm_encounter(AS_OF + timedelta(days=30), icd10="I50.9")])
    profile = build_patient_clinical_profile(state)
    report = identify_complications(profile)
    assert report.findings == ()


def test_complication_identification_ignores_future_dated_ckd_assessment():
    """回歸測試（Codex #8，CKD 分期分支）：`_latest_ckd_stage()` 先前對
    `ckd_assessments` 取全域最大日期，未限制在 <= as_of，未來日期的評估
    會蓋過真正最新（但在 as_of 之前）的評估。"""
    state = make_state(
        encounters=[dm_encounter(AS_OF - timedelta(days=10), icd10="E11.21")],
        ckd_assessments=[
            CKDAssessment(AS_OF - timedelta(days=10), egfr=50.0, upcr=200.0, is_diabetic=True),  # → "3a"
            CKDAssessment(AS_OF + timedelta(days=30), egfr=10.0, upcr=200.0, is_diabetic=True),  # 未來，不應採用
        ],
    )
    profile = build_patient_clinical_profile(state)
    report = identify_complications(profile)
    nephropathy = next(f for f in report.findings if f.category == "NEPHROPATHY")
    assert nephropathy.ckd_stage == "3a"


def test_complication_identification_lookback_years_survives_leap_day():
    """回歸測試（Codex #9）：as_of 為 2/29 時，`as_of.replace(year=...)`
    對非閏年目標年會拋 ValueError；改用 `_safe_years_before()` 落回 2/28。"""
    state = make_state(
        as_of_date=date(2024, 2, 29),
        encounters=[dm_encounter(date(2024, 1, 15), icd10="E11.21")],
    )
    profile = build_patient_clinical_profile(state)
    report = identify_complications(profile, ComplicationConfig(lookback_years=1))  # 2023 非閏年
    assert report.as_of_date == date(2024, 2, 29)


# ---------------------------------------------------------------------------
# 4. 風險計算
# ---------------------------------------------------------------------------


def test_risk_overall_level_high_when_hba1c_poor_and_complication_present():
    state = make_state(
        encounters=[dm_encounter(AS_OF, icd10="E11.21")],
        lab_results=[
            LabResult("09006C", AS_OF - timedelta(days=90), value=9.5),
            LabResult("09006C", AS_OF - timedelta(days=60), value=9.8),
            LabResult("09006C", AS_OF - timedelta(days=10), value=10.2),
        ],
    )
    profile = build_patient_clinical_profile(state)
    trend_report = analyze_clinical_trends(profile)
    complication_report = identify_complications(profile)
    risk_result = assess_risk(profile, trend_report, complication_report)

    assert risk_result.overall_risk_level == RiskLevel.HIGH
    assert risk_result.is_placeholder_methodology is True  # 鐵律1：必須顯示 placeholder 旗標

    hba1c_contribution = next(c for c in risk_result.contributions if c.factor == "hba1c")
    assert hba1c_contribution.level == RiskLevel.HIGH


# ---------------------------------------------------------------------------
# 5. Care Gap
# ---------------------------------------------------------------------------


def test_care_gap_detects_missing_lab_items():
    state = make_state(
        claims=[CodeClaim("P1407C", AS_OF - timedelta(days=200))],
        lab_results=[
            LabResult("09006C", AS_OF - timedelta(days=10), value=8.0),
            # 缺 09005C（P1408C 的另一必要項目）
        ],
    )
    profile = build_patient_clinical_profile(state)
    report = assess_care_gaps(profile, codes_in_scope=["P1408C"], include_quality_monitoring=False)

    assert "P1408C" in report.unresolved_codes
    items = report.by_code["P1408C"]
    unsatisfied_descriptions = {it.requirement.description for it in items if not it.satisfied}
    assert any("09005C" in desc for desc in unsatisfied_descriptions)

    satisfied_item = next(it for it in items if "09006C" in it.requirement.alternatives)
    assert satisfied_item.satisfied is True
    assert satisfied_item.spec_reference  # 整合新增欄位：必須有出處


def test_care_gap_all_satisfied_yields_no_unresolved():
    state = make_state(
        claims=[CodeClaim("P1407C", AS_OF - timedelta(days=200))],
        lab_results=[
            LabResult("09006C", AS_OF - timedelta(days=10), value=8.0),
            LabResult("09005C", AS_OF - timedelta(days=10), value=100.0),
        ],
    )
    profile = build_patient_clinical_profile(state)
    report = assess_care_gaps(profile, codes_in_scope=["P1408C"], include_quality_monitoring=False)
    assert "P1408C" not in report.unresolved_codes


def test_care_gap_reports_unregistered_codes_explicitly():
    state = make_state()
    profile = build_patient_clinical_profile(state)
    report = assess_care_gaps(profile, codes_in_scope=["P1410C"], include_quality_monitoring=False)
    assert "P1410C" in report.unregistered_codes
    assert report.warnings  # 不可靜默視為「無缺漏」


def test_care_gap_ignores_future_dated_lab_as_most_recent_ever():
    """回歸測試（Codex #10）：`most_recent_ever`（供逾期天數/UI顯示用）先前
    未排除未來日期的檢驗結果，會被當成「史上最新一筆」，產生負的
    days_since_last，UI 上顯示成「已逾期」卻其實是還沒發生的未來日期。"""
    state = make_state(
        claims=[CodeClaim("P1407C", AS_OF - timedelta(days=200))],
        lab_results=[
            LabResult("09006C", AS_OF - timedelta(days=400), value=8.0),  # 早於視窗，未滿足but可作history
            LabResult("09006C", AS_OF + timedelta(days=10), value=7.0),  # 未來日期，不應被採用
        ],
    )
    profile = build_patient_clinical_profile(state)
    report = assess_care_gaps(profile, codes_in_scope=["P1408C"], include_quality_monitoring=False)

    items = report.by_code["P1408C"]
    hba1c_item = next(it for it in items if "09006C" in it.requirement.alternatives)
    assert hba1c_item.most_recent_ever is not None
    assert hba1c_item.most_recent_ever.result_date == AS_OF - timedelta(days=400)
    assert hba1c_item.days_since_last == 400


def test_care_gap_quality_monitoring_requires_same_day_dm_encounter_with_a10():
    """回歸測試（Codex #4）：品質監測（180天強制檢驗排程）只有「當次(as_of)
    確實有一筆 DM 診斷 + A10 藥物並存的就診」才會觸發（見
    dm_eligibility.rules_p14.check_quality_monitoring() docstring 逐字出處）。
    先前 include_quality_monitoring=True（預設值）時無條件產生四類強制
    排程項目，完全沒有就診紀錄的病人也會收到品質監測缺漏。"""
    state = make_state()  # 無 encounters
    profile = build_patient_clinical_profile(state)
    report = assess_care_gaps(profile, codes_in_scope=[], include_quality_monitoring=True)
    assert _QUALITY_MONITORING_PSEUDO_CODE not in report.by_code


def test_care_gap_quality_monitoring_triggers_on_matching_encounter():
    """正向對照：當次就診確實是 DM 診斷 + A10 藥物時，應觸發品質監測，且
    血脂四項需拆成 4 個各自獨立項目（Codex #5）——單一總膽固醇不足以
    滿足整組。"""
    state = make_state(encounters=[dm_encounter(AS_OF, icd10="E11.9", with_med=True)])
    profile = build_patient_clinical_profile(state)
    report = assess_care_gaps(profile, codes_in_scope=[], include_quality_monitoring=True)

    assert _QUALITY_MONITORING_PSEUDO_CODE in report.by_code
    items = report.by_code[_QUALITY_MONITORING_PSEUDO_CODE]
    lipid_descriptions = {it.requirement.description for it in items if "血脂四項" in it.requirement.description}
    assert len(lipid_descriptions) == 4  # 4 個各自獨立項目，非合併成一項


# ---------------------------------------------------------------------------
# 6. Guideline Recommendation
# ---------------------------------------------------------------------------


def test_guideline_recommendation_has_grounded_evidence():
    state = make_state(
        encounters=[dm_encounter(AS_OF, icd10="E11.9")],
        lab_results=[
            LabResult("09006C", AS_OF - timedelta(days=90), value=9.5),
            LabResult("09006C", AS_OF - timedelta(days=60), value=9.8),
            LabResult("09006C", AS_OF - timedelta(days=10), value=10.0),
        ],
    )
    profile = build_patient_clinical_profile(state)
    trend_report = analyze_clinical_trends(profile)
    complication_report = identify_complications(profile)
    risk_result = assess_risk(profile, trend_report, complication_report)
    care_gap_report = assess_care_gaps(profile, codes_in_scope=[], include_quality_monitoring=False)

    guideline_input = build_guideline_input(profile, trend_report, complication_report, risk_result, care_gap_report)
    report = GuidelineRecommendationEngine().build(guideline_input)

    hba1c_rec = next(r for r in report.recommendations if r.rule_id == "HBA1C_POOR_NO_RECENT_TRACKING")
    assert hba1c_rec.evidence  # 每條建議都必須有依據欄位
    assert all(e.detail for e in hba1c_rec.evidence)
    assert hba1c_rec.trigger_grounded_in_spec is True
    assert hba1c_rec.action_is_placeholder_content is True  # 建議動作文字非規格明文，需標示


def test_guideline_renal_rule_requires_and_condition():
    """RENAL_COMPLICATION_HIGH_RISK 需同時符合「腎併發症」AND「風險計算判定
    ckd_stage=HIGH」；只有腎併發症、但風險未判為高風險時不應觸發。"""
    state = make_state(
        encounters=[dm_encounter(AS_OF, icd10="E11.21")],
        ckd_assessments=[CKDAssessment(AS_OF, egfr=95.0, upcr=200.0, is_diabetic=True)],  # Stage1，非高風險stage
    )
    profile = build_patient_clinical_profile(state)
    trend_report = analyze_clinical_trends(profile)
    complication_report = identify_complications(profile)
    risk_result = assess_risk(profile, trend_report, complication_report)
    care_gap_report = assess_care_gaps(profile, codes_in_scope=[], include_quality_monitoring=False)
    guideline_input = build_guideline_input(profile, trend_report, complication_report, risk_result, care_gap_report)
    report = GuidelineRecommendationEngine().build(guideline_input)

    assert not any(r.rule_id == "RENAL_COMPLICATION_HIGH_RISK" for r in report.recommendations)


# ---------------------------------------------------------------------------
# 7. 醫師決策
# ---------------------------------------------------------------------------


def _sample_guideline_report():
    state = make_state(
        lab_results=[
            LabResult("09006C", AS_OF - timedelta(days=90), value=9.5),
            LabResult("09006C", AS_OF - timedelta(days=60), value=9.8),
            LabResult("09006C", AS_OF - timedelta(days=10), value=10.0),
        ]
    )
    profile = build_patient_clinical_profile(state)
    trend_report = analyze_clinical_trends(profile)
    complication_report = identify_complications(profile)
    risk_result = assess_risk(profile, trend_report, complication_report)
    care_gap_report = assess_care_gaps(profile, codes_in_scope=[], include_quality_monitoring=False)
    guideline_input = build_guideline_input(profile, trend_report, complication_report, risk_result, care_gap_report)
    return GuidelineRecommendationEngine().build(guideline_input)


def test_physician_decision_record_structure_accept_modify_decline():
    report = _sample_guideline_report()
    assert len(report.recommendations) >= 1
    record = present_for_decision(report)

    # 全部起始應為 PENDING
    assert record.pending_count() == len(report.recommendations)
    assert record.is_fully_reviewed() is False

    rec = report.recommendations[0]
    record.record_decision(PhysicianDecision(recommendation_id=rec.recommendation_id, status=PhysicianDecisionStatus.ACCEPTED, physician_id="DOC1"))

    if len(report.recommendations) > 1:
        rec2 = report.recommendations[1]
        record.record_decision(
            PhysicianDecision(
                recommendation_id=rec2.recommendation_id,
                status=PhysicianDecisionStatus.MODIFIED,
                modified_action_text="改為安排轉診",
                physician_id="DOC1",
            )
        )

    # 婉拒必須有理由，否則 raise
    with pytest.raises(DecisionValidationError):
        record.record_decision(PhysicianDecision(recommendation_id=rec.recommendation_id, status=PhysicianDecisionStatus.DECLINED))

    # 修改必須有 modified_action_text，否則 raise
    with pytest.raises(DecisionValidationError):
        record.record_decision(PhysicianDecision(recommendation_id=rec.recommendation_id, status=PhysicianDecisionStatus.MODIFIED))

    # 不存在的 recommendation_id 應 raise
    with pytest.raises(DecisionValidationError):
        record.record_decision(PhysicianDecision(recommendation_id="不存在的ID", status=PhysicianDecisionStatus.ACCEPTED))

    accepted_or_modified = record.accepted_or_modified()
    accepted_ids = {r.recommendation_id for r, _d in accepted_or_modified}
    assert rec.recommendation_id in accepted_ids


def test_physician_decision_no_auto_approval_path():
    """鐵律3：沒有任何自動核准路徑——只呼叫 present_for_decision() 且不手動
    record_decision()，所有建議必須維持 PENDING，accepted_or_modified() 必須
    是空清單。"""
    report = _sample_guideline_report()
    record = present_for_decision(report)
    assert record.accepted_or_modified() == []
    assert record.pending_count() == len(report.recommendations)
    assert not hasattr(record, "auto_approve")


# ---------------------------------------------------------------------------
# 8. 病人衛教
# ---------------------------------------------------------------------------


def test_education_topic_selection_from_complication_and_decision():
    state = make_state(encounters=[dm_encounter(AS_OF, icd10="E11.51")])  # PVD → FOOT_CARE_BASIC
    profile = build_patient_clinical_profile(state)
    complication_report = identify_complications(profile)
    assert any(f.category == "PVD" for f in complication_report.findings)

    report = _sample_guideline_report()
    decision_record = present_for_decision(report)
    hba1c_rec = next(r for r in report.recommendations if r.rule_id == "HBA1C_POOR_NO_RECENT_TRACKING")
    decision_record.record_decision(
        PhysicianDecision(recommendation_id=hba1c_rec.recommendation_id, status=PhysicianDecisionStatus.ACCEPTED, physician_id="DOC1")
    )

    plan = select_education_topics(decision_record, complication_report)
    topic_codes = {t.topic_code for t in plan.topics}

    assert "FOOT_CARE_BASIC" in topic_codes  # 由併發症驅動
    assert "GLYCEMIC_CONTROL_BASIC" in topic_codes  # 由醫師已核可建議驅動


def test_education_topic_missing_resource_flags_manual_review():
    cfg = EducationTopicMappingConfig(
        complication_to_topics={"NEPHROPATHY": ("NO_SUCH_TOPIC",)},
        resources_by_topic={},
    )
    state = make_state(
        encounters=[dm_encounter(AS_OF, icd10="E11.21")],
        ckd_assessments=[CKDAssessment(AS_OF, egfr=50.0, upcr=200.0, is_diabetic=True)],
    )
    profile = build_patient_clinical_profile(state)
    complication_report = identify_complications(profile)
    report = _sample_guideline_report()
    decision_record = present_for_decision(report)

    plan = select_education_topics(decision_record, complication_report, cfg)
    assert plan.needs_manual_review is True
    assert plan.warnings


# ---------------------------------------------------------------------------
# 9. 後續追蹤
# ---------------------------------------------------------------------------


def test_followup_next_visit_date_from_last_p1408_claim():
    last_p1408 = AS_OF - timedelta(days=80)
    state = make_state(claims=[CodeClaim("P1407C", AS_OF - timedelta(days=300)), CodeClaim("P1408C", last_p1408)])
    profile = build_patient_clinical_profile(state)
    complication_report = identify_complications(profile)

    plan = compute_follow_up_plan(profile, complication_report, assume_eligible_codes_claimed_today=False)

    expected = last_p1408 + timedelta(days=SUBSEQUENT_TRACKING_INTERVAL_DAYS)
    assert plan.next_code_due_dates["P1408C"] == expected
    assert plan.next_recommended_visit_date == expected
    assert any("P1408C" in r for r in plan.reasons)


def test_followup_falls_back_when_no_due_date_computable():
    state = make_state()  # 完全無 claims/complications
    profile = build_patient_clinical_profile(state)
    complication_report = identify_complications(profile)

    plan = compute_follow_up_plan(profile, complication_report, assume_eligible_codes_claimed_today=False)

    assert plan.next_recommended_visit_date == AS_OF + timedelta(days=90)  # fallback_visit_interval_days 預設值
    assert plan.warnings  # 必須顯式標記使用了 fallback


# ---------------------------------------------------------------------------
# 端到端：pipeline.py
# ---------------------------------------------------------------------------


def test_pipeline_end_to_end_integration():
    encounters = [dm_encounter(AS_OF - timedelta(days=100), icd10="E11.21"), dm_encounter(AS_OF, icd10="E11.21")]
    lab_results = [
        LabResult("09006C", AS_OF - timedelta(days=10), value=9.5),
        LabResult("09005C", AS_OF - timedelta(days=10), value=180.0),
    ]
    state = make_state(
        encounters=encounters,
        lab_results=lab_results,
        claims=[CodeClaim("P1407C", AS_OF - timedelta(days=100))],
        ckd_assessments=[CKDAssessment(AS_OF - timedelta(days=10), egfr=50.0, upcr=200.0, is_diabetic=True)],
        vpn_other_institution_enrolled=False,
        age_years=55,
    )
    physician = PhysicianStatus(physician_id="DOC1", is_dm_ckd_dual_qualified=True)
    eligibility_report = EligibilityEngine().evaluate(state, physician)

    run_result = run_stages_1_to_7(state, eligibility_report=eligibility_report, physician=physician)
    # v2 起 decision_record 合併 GuidelineRecommendation 與
    # MedicationRecommendation（架構文件v2 3.14節 Reviewable 合流），
    # pending_count() 不再只等於 guideline_report.recommendations 筆數。
    assert run_result.decision_record.pending_count() == len(run_result.guideline_report.recommendations) + len(
        run_result.medication_report.recommendations
    )

    for rec in run_result.decision_record.presented_recommendations:
        run_result.decision_record.record_decision(
            PhysicianDecision(recommendation_id=rec.recommendation_id, status=PhysicianDecisionStatus.ACCEPTED, physician_id="DOC1")
        )

    final_result = finalize_pipeline(run_result)
    assert final_result.followup_plan.next_recommended_visit_date >= AS_OF
    assert isinstance(final_result.education_plan.topics, list)


def test_pipeline_default_scope_surfaces_gap_that_causes_ineligibility():
    """回歸測試（Codex #3）：P1408C 因缺 09005C 而 ineligible 時，
    `codes_in_scope` 預設值先前是 `eligible_codes()`——P1408C 根本不在
    其中，這筆本應是「care gap 最有價值用途」的缺漏（缺這項檢驗導致
    無法達成資格）永遠不會出現在 care_gap_report。改用
    `eligibility_report.results` 全部代碼後，即使 P1408C 尚未 eligible，
    它的必要檢驗仍應被檢查、缺漏應出現在 care_gap_report。"""
    state = make_state(
        encounters=[dm_encounter(AS_OF, icd10="E11.21")],
        claims=[CodeClaim("P1407C", AS_OF - timedelta(days=100))],
        lab_results=[
            LabResult("09006C", AS_OF - timedelta(days=10), value=8.0),
            # 刻意缺 09005C（P1408C 另一必要項目）→ P1408C 應為 ineligible
        ],
    )
    eligibility_report = EligibilityEngine().evaluate(state)
    assert eligibility_report.get("P1408C").eligible is False
    assert "P1408C" not in eligibility_report.eligible_codes()

    run_result = run_stages_1_to_7(state, eligibility_report=eligibility_report)

    assert "P1408C" in run_result.care_gap_report.by_code
    unsatisfied = {
        it.requirement.description for it in run_result.care_gap_report.by_code["P1408C"] if not it.satisfied
    }
    assert any("09005C" in desc for desc in unsatisfied)
