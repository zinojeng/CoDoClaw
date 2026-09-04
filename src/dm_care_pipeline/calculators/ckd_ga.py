"""
Tier A — CKD G/A Classification（KDIGO 標準分期）。

★ 鐵律1：G1-G5（eGFR切點 G1≥90/G2 60-89/G3a 45-59/G3b 30-44/G4 15-29/
G5<15 mL/min/1.73m²）與 A1-A3（A1<30/A2 30-300/A3>300 mg/g）為國際通用
KDIGO 標準分期表，OpenClaw for Diabetes HIS.md §6.1 已引用（僅列出
G1-G5/A1-A3 分期名稱本身，未逐字重列切點數字，切點數字依任務指示採用
KDIGO 國際標準表），照原文切點實作，不可自行調整數值。

★ 本 calculator 刻意**不**重用 `dm_eligibility.CKDAssessment.stage()`——
後者是 P7 spec CKD-ENROLL 收案資格判斷用的 "1"/"2"/"3a" 三級子集（用途：
收案資格），與本檔案完整 G1-G5×A1-A3 分期（用途：臨床嚴重度顯示）是
**兩套並存、用途不同**的分期邏輯，不可互相取代（見架構文件v2 第5節
open_questions#2）。本 calculator 只重用 `CKDAssessment` 的 eGFR/UACR
原始欄位存取慣例，不呼叫其 `.stage()`。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import ClassVar, Optional

from ..clinical_data_object import ClinicalStatus
from .base import (
    CalculatorExecutionStatus,
    CalculatorInputField,
    CalculatorResult,
    CalculatorTier,
)

CALCULATOR_ID = "KDIGO_GA"
CALCULATOR_VERSION = "v1.0"
SPEC_REFERENCE = "OpenClaw for Diabetes HIS.md §6.1；G1-G5/A1-A3 為國際通用 KDIGO 標準分期表"
GUIDELINE_ID = "KDIGO"


@dataclass(frozen=True)
class CKDGAInputs:
    patient_id: str
    as_of: date
    egfr: Optional[float] = None
    uacr: Optional[float] = None
    egfr_date: Optional[date] = None
    uacr_date: Optional[date] = None
    # ★ 具名旗標（架構文件v2 3.3節/第5節#3）：單次 eGFR/UACR 異常僅
    # SUSPECTED，需臨床端另外提供「已有對應 ICD-10 診斷佐證」（例如
    # complication_identification.py 命中 NEPHROPATHY 類別）才升級為
    # CONFIRMED。是否應改為 KDIGO chronicity 定義（>3個月、兩次分期一致）
    # 需臨床覆核，本欄位僅為呼叫端傳入既有佐證與否的旗標。
    corroborating_ckd_diagnosis: bool = False


def _g_stage(egfr: float) -> str:
    if egfr >= 90:
        return "G1"
    if egfr >= 60:
        return "G2"
    if egfr >= 45:
        return "G3a"
    if egfr >= 30:
        return "G3b"
    if egfr >= 15:
        return "G4"
    return "G5"


def _a_stage(uacr: float) -> str:
    if uacr < 30:
        return "A1"
    if uacr <= 300:
        return "A2"
    return "A3"


class CKDGACalculator:
    calculator_id: ClassVar[str] = CALCULATOR_ID
    calculator_version: ClassVar[str] = CALCULATOR_VERSION
    tier: ClassVar[CalculatorTier] = CalculatorTier.A
    required_inputs: ClassVar[tuple[str, ...]] = ("egfr", "uacr")

    def compute(self, inputs: CKDGAInputs) -> CalculatorResult:
        input_fields = (
            CalculatorInputField(
                name="egfr", provided=inputs.egfr is not None, value=inputs.egfr, observed_date=inputs.egfr_date
            ),
            CalculatorInputField(
                name="uacr", provided=inputs.uacr is not None, value=inputs.uacr, observed_date=inputs.uacr_date
            ),
        )

        if inputs.egfr is None and inputs.uacr is None:
            return CalculatorResult(
                calculator_id=self.calculator_id,
                calculator_version=self.calculator_version,
                tier=self.tier,
                patient_id=inputs.patient_id,
                computed_at=inputs.as_of,
                execution_status=CalculatorExecutionStatus.INSUFFICIENT_DATA,
                inputs=input_fields,
                missing_inputs=("egfr", "uacr"),
                spec_reference=SPEC_REFERENCE,
                warnings=("eGFR 與 UACR 皆缺，無法進行 KDIGO G/A 分期",),
            )

        warnings: list[str] = []
        missing_inputs: list[str] = []
        g_stage = _g_stage(inputs.egfr) if inputs.egfr is not None else None
        a_stage = _a_stage(inputs.uacr) if inputs.uacr is not None else None
        if g_stage is None:
            missing_inputs.append("egfr")
            warnings.append("eGFR 缺值，僅能提供 Albuminuria 分期，G/A 分期不完整")
        if a_stage is None:
            missing_inputs.append("uacr")
            warnings.append("UACR 缺值，僅能提供 G 分期，G/A 分期不完整")

        result_summary = f"CKD {g_stage or 'G?'}{a_stage or 'A?'}"
        result_values: dict[str, object] = {"g_stage": g_stage, "a_stage": a_stage, "egfr": inputs.egfr, "uacr": inputs.uacr}

        # ★ 修正（Codex #13）：KDIGO CKD 定義是「eGFR<60（G3a以下）」或
        # 「腎損傷標記（含 A2/A3 白蛋白尿）持續≥3個月」二擇一成立才算
        # CKD。G2（eGFR 60-89）本身不是腎功能異常的判準——若同時 A1（無
        # 白蛋白尿、無其他腎損傷標記），G2A1 不符合 CKD 定義，只有 G1A1
        # 才是。先前只把 G1A1 視為正常，導致單純因年齡等因素 eGFR 落在
        # 60-89 區間、UACR 正常的病人也被標記 SUSPECTED CKD。
        is_normal = g_stage in ("G1", "G2") and a_stage == "A1"
        clinical_status: Optional[ClinicalStatus] = None
        if g_stage is not None and a_stage is not None and not is_normal:
            clinical_status = ClinicalStatus.CONFIRMED if inputs.corroborating_ckd_diagnosis else ClinicalStatus.SUSPECTED
        elif (g_stage is not None) != (a_stage is not None):
            # 只有單一軸可判定：保守起見，若該軸本身已顯示異常，仍標記
            # SUSPECTED（不因缺另一軸資料而silently視為正常）；若唯一可得
            # 的軸為正常值（G1/G2 或 A1，與上方 is_normal 同一判準），因
            # 無法排除另一軸異常，不給 clinical_status（留待資料補齊）。
            available_abnormal = (g_stage is not None and g_stage not in ("G1", "G2")) or (
                a_stage is not None and a_stage != "A1"
            )
            if available_abnormal:
                clinical_status = ClinicalStatus.CONFIRMED if inputs.corroborating_ckd_diagnosis else ClinicalStatus.SUSPECTED

        return CalculatorResult(
            calculator_id=self.calculator_id,
            calculator_version=self.calculator_version,
            tier=self.tier,
            patient_id=inputs.patient_id,
            computed_at=inputs.as_of,
            execution_status=CalculatorExecutionStatus.COMPUTED,
            inputs=input_fields,
            missing_inputs=tuple(missing_inputs),
            result_values=result_values,
            result_summary=result_summary,
            clinical_status=clinical_status,
            guideline=GUIDELINE_ID,
            spec_reference=SPEC_REFERENCE,
            is_placeholder_methodology=False,
            action_grounded_in_spec=False,
            warnings=tuple(warnings),
        )
