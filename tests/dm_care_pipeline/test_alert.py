"""`alert.py`（規格§32 三級 Alert 分級）測試。"""

from __future__ import annotations

from datetime import date, datetime

from dm_care_pipeline.alert import (
    AlertClassificationConfig,
    AlertLevel,
    classify_alert,
    classify_alert_batch,
)
from dm_care_pipeline.clinical_data_object import ClinicalDomain, ClinicalFinding, ClinicalStatus

AS_OF = date(2024, 6, 1)


def make_finding(status: ClinicalStatus, finding_id: str = "f1") -> ClinicalFinding:
    return ClinicalFinding(
        finding_id=finding_id,
        patient_id="P1",
        domain=ClinicalDomain.KIDNEY,
        condition="CKD",
        status=status,
        date=AS_OF,
        generated_at=datetime.now(),
    )


# ---------------------------------------------------------------------------
# classify_alert()
# ---------------------------------------------------------------------------


def test_override_forces_safety_alert_regardless_of_status():
    finding = make_finding(ClinicalStatus.SUSPECTED)
    assert classify_alert(finding, is_safety_critical_override=True) == AlertLevel.SAFETY_ALERT


def test_category_in_safety_alert_categories_triggers_safety_alert():
    finding = make_finding(ClinicalStatus.SUSPECTED)
    assert classify_alert(finding, category="SEVERE_HYPOGLYCEMIA_RISK") == AlertLevel.SAFETY_ALERT


def test_category_not_in_safety_alert_categories_does_not_trigger():
    finding = make_finding(ClinicalStatus.CARE_GAP)
    assert classify_alert(finding, category="SOME_UNKNOWN_CATEGORY") == AlertLevel.CLINICAL_ATTENTION


def test_high_risk_status_is_clinical_attention():
    finding = make_finding(ClinicalStatus.HIGH_RISK)
    assert classify_alert(finding) == AlertLevel.CLINICAL_ATTENTION


def test_care_gap_status_is_clinical_attention():
    finding = make_finding(ClinicalStatus.CARE_GAP)
    assert classify_alert(finding) == AlertLevel.CLINICAL_ATTENTION


def test_confirmed_status_defaults_to_information():
    finding = make_finding(ClinicalStatus.CONFIRMED)
    assert classify_alert(finding) == AlertLevel.INFORMATION


def test_suspected_status_defaults_to_information():
    finding = make_finding(ClinicalStatus.SUSPECTED)
    assert classify_alert(finding) == AlertLevel.INFORMATION


def test_custom_config_can_reclassify_confirmed_as_clinical_attention():
    cfg = AlertClassificationConfig(clinical_attention_status=frozenset({ClinicalStatus.CONFIRMED}))
    finding = make_finding(ClinicalStatus.CONFIRMED)
    assert classify_alert(finding, config=cfg) == AlertLevel.CLINICAL_ATTENTION


def test_config_is_not_exhaustive_by_default():
    assert AlertClassificationConfig().is_exhaustive is False


def test_safety_alert_categories_has_five_spec_examples():
    cfg = AlertClassificationConfig()
    assert cfg.safety_alert_categories == frozenset(
        {
            "MEDICATION_CONTRAINDICATION",
            "SEVERE_HYPOGLYCEMIA_RISK",
            "DANGEROUS_LAB_RESULT",
            "MAJOR_DRUG_INTERACTION",
            "ORDER_CONFLICT",
        }
    )


# ---------------------------------------------------------------------------
# classify_alert_batch()
# ---------------------------------------------------------------------------


def test_batch_groups_findings_by_level():
    findings = [
        make_finding(ClinicalStatus.CONFIRMED, "f1"),
        make_finding(ClinicalStatus.HIGH_RISK, "f2"),
        make_finding(ClinicalStatus.CARE_GAP, "f3"),
    ]
    report = classify_alert_batch(findings)
    assert len(report.by_level[AlertLevel.INFORMATION]) == 1
    assert len(report.by_level[AlertLevel.CLINICAL_ATTENTION]) == 2
    assert report.safety_alert_count == 0


def test_batch_safety_alert_count_only_counts_safety_level():
    findings = [
        make_finding(ClinicalStatus.SUSPECTED, "f1"),
        make_finding(ClinicalStatus.HIGH_RISK, "f2"),
    ]
    report = classify_alert_batch(findings, categories={"f1": "MAJOR_DRUG_INTERACTION"})
    assert report.safety_alert_count == 1
    assert report.by_level[AlertLevel.SAFETY_ALERT][0].finding_id == "f1"


def test_batch_derives_patient_id_and_date_from_first_finding():
    findings = [make_finding(ClinicalStatus.CONFIRMED, "f1")]
    report = classify_alert_batch(findings)
    assert report.patient_id == "P1"
    assert report.as_of_date == AS_OF


def test_batch_empty_findings_yields_empty_placeholders_not_fabricated_date():
    report = classify_alert_batch([])
    assert report.patient_id == ""
    assert report.as_of_date is None
    assert report.safety_alert_count == 0
    assert all(v == [] for v in report.by_level.values())


def test_batch_explicit_patient_id_and_as_of_date_override_inference():
    """回歸測試（Codex #30）：`findings[0].date` 是證據日期（例如檢驗抽血
    日），可能是好幾年前，不是「本次評估」的日期——呼叫端若明確知道真正
    的 patient_id/as_of_date（例如 pre_visit_brief.py 手上的
    profile.patient_id/profile.as_of_date），應優先採用，而非從證據日期
    反推。"""
    old_evidence_date = date(2019, 1, 1)
    findings = [
        ClinicalFinding(
            finding_id="f1",
            patient_id="P1",
            domain=ClinicalDomain.KIDNEY,
            condition="CKD",
            status=ClinicalStatus.CONFIRMED,
            date=old_evidence_date,
            generated_at=datetime.now(),
        )
    ]
    report = classify_alert_batch(findings, patient_id="P1", as_of_date=AS_OF)
    assert report.as_of_date == AS_OF
    assert report.as_of_date != old_evidence_date


def test_batch_explicit_patient_id_and_as_of_date_used_even_with_no_findings():
    """回歸測試（Codex #30）：`findings` 為空時先前一律回傳
    `patient_id=""`/`as_of_date=None`，即使呼叫端明明知道正確答案（例如
    完全無 finding 的病人，pre_visit_brief.py 仍知道是哪位病人、哪一天
    評估）。"""
    report = classify_alert_batch([], patient_id="P1", as_of_date=AS_OF)
    assert report.patient_id == "P1"
    assert report.as_of_date == AS_OF


def test_batch_every_alert_level_key_present_even_when_empty():
    report = classify_alert_batch([make_finding(ClinicalStatus.CONFIRMED)])
    assert set(report.by_level.keys()) == set(AlertLevel)
