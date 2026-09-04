"""
Tier A — IWGDF Diabetic Foot Risk Classification。

★ 鐵律1：Category 0-3 條件與追蹤頻率逐字取自 OpenClaw for Diabetes
HIS.md §10：
    Category 0（Very low）：No LOPS and No PAD → 每年一次
    Category 1（Low）：LOPS 或 PAD → 每 6–12 個月
    Category 2（Moderate）：LOPS+PAD 或 LOPS+foot deformity 或
        PAD+foot deformity → 每 3–6 個月
    Category 3（High）：（LOPS 或 PAD）+ (previous foot ulcer 或
        previous amputation 或 kidney failure) → 每 1–3 個月

`IWGDF_FOLLOWUP_INTERVAL_DAYS` 為本檔案（IWGDF calculator 自身）的唯一
權威來源，`care_gap_clocks.py` 等其餘模組一律 import 使用，不重複宣告
（架構文件v2 第2節命名統一總表）。

★ 具名工程詮釋（架構文件v2 第5節 open_questions#17）：規格書給的是區間
（如「6-12個月」）而非單一到期日切點。本檔案對「上次評估後是否已逾期」
採用**區間上界（較寬鬆的一端）**作為單一 overdue 判定切點，即超過區間
上界才判定 overdue（`OVERDUE_USES_UPPER_BOUND=True`），避免在區間內就
過早發出提醒造成 alert fatigue；此為工程保守選擇，非規格逐字定義，正式
上線前需院內排程政策確認（見 open_questions#17）。
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

CALCULATOR_ID = "IWGDF_FOOT_RISK"
CALCULATOR_VERSION = "v1.0"
SPEC_REFERENCE = "OpenClaw for Diabetes HIS.md §10"
GUIDELINE_ID = "IWGDF_2023"

# category → (min_days, max_days)。唯一權威來源，其餘模組（care_gap_clocks.py
# 等）一律 import 使用，不重複宣告。
IWGDF_FOLLOWUP_INTERVAL_DAYS: dict[int, tuple[int, int]] = {
    0: (365, 365),
    1: (180, 365),
    2: (90, 180),
    3: (30, 90),
}

# ★ 具名旗標：overdue 判定採用區間上界（較寬鬆的一端），見模組 docstring。
OVERDUE_USES_UPPER_BOUND = True


@dataclass(frozen=True)
class IWGDFFootInputs:
    patient_id: str
    as_of: date
    lops_present: Optional[bool] = None
    pad_present: Optional[bool] = None
    foot_deformity_present: Optional[bool] = None
    previous_foot_ulcer: Optional[bool] = None
    previous_amputation: Optional[bool] = None
    kidney_failure_present: Optional[bool] = None
    last_foot_evaluation_date: Optional[date] = None


def _determine_category(inputs: IWGDFFootInputs) -> int:
    lops = bool(inputs.lops_present)
    pad = bool(inputs.pad_present)
    deformity = bool(inputs.foot_deformity_present)
    high_risk_history = bool(inputs.previous_foot_ulcer) or bool(inputs.previous_amputation) or bool(inputs.kidney_failure_present)

    if (lops or pad) and high_risk_history:
        return 3
    if (lops and pad) or (lops and deformity) or (pad and deformity):
        return 2
    if lops or pad:
        return 1
    return 0


class IWGDFFootRiskCalculator:
    calculator_id: ClassVar[str] = CALCULATOR_ID
    calculator_version: ClassVar[str] = CALCULATOR_VERSION
    tier: ClassVar[CalculatorTier] = CalculatorTier.A
    required_inputs: ClassVar[tuple[str, ...]] = ("lops_present", "pad_present")

    def compute(self, inputs: IWGDFFootInputs) -> CalculatorResult:
        field_names = (
            "lops_present",
            "pad_present",
            "foot_deformity_present",
            "previous_foot_ulcer",
            "previous_amputation",
            "kidney_failure_present",
        )
        input_fields = tuple(
            CalculatorInputField(name=name, provided=getattr(inputs, name) is not None, value=getattr(inputs, name))
            for name in field_names
        ) + (
            CalculatorInputField(
                name="last_foot_evaluation_date",
                provided=inputs.last_foot_evaluation_date is not None,
                value=inputs.last_foot_evaluation_date,
            ),
        )

        # ★ 規格§10 的 Category 0（No LOPS and No PAD）與其他 Category
        # 皆以 LOPS/PAD 是否存在為第一層判斷依據；LOPS/PAD 未評估時不可
        # 默視為「無」（那會把「沒查」誤判成 Category 0/最低風險）。
        if inputs.lops_present is None or inputs.pad_present is None:
            missing = tuple(
                name for name in ("lops_present", "pad_present") if getattr(inputs, name) is None
            )
            return CalculatorResult(
                calculator_id=self.calculator_id,
                calculator_version=self.calculator_version,
                tier=self.tier,
                patient_id=inputs.patient_id,
                computed_at=inputs.as_of,
                execution_status=CalculatorExecutionStatus.INSUFFICIENT_DATA,
                inputs=input_fields,
                missing_inputs=missing,
                spec_reference=SPEC_REFERENCE,
                warnings=("LOPS/PAD 未評估，不可默視為無，無法判定 IWGDF Category",),
            )

        warnings: list[str] = []
        # ★ 修正（Codex #18）：先前這幾個欄位缺值時只寫進 warnings（自由
        # 文字），missing_inputs 卻恆為空 tuple——任何依結構化 missing_inputs
        # 判斷「資料是否齊全」的下游消費者（而非解析 warnings 文字）完全
        # 看不出這個 Category 是在關鍵決定因子未知的情況下算出來的。若
        # LOPS/PAD 存在但 ulcer/amputation/kidney_failure 未知，真實
        # Category 可能是 3（高風險），卻因未知值被當「無」而算成 1 或 2。
        unknown_determinants: list[str] = []
        for name in ("foot_deformity_present", "previous_foot_ulcer", "previous_amputation", "kidney_failure_present"):
            if getattr(inputs, name) is None:
                unknown_determinants.append(name)
                warnings.append(f"{name} 未評估，Category 判定可能低估（保守處理為『無』，見 open_questions#8）")

        category = _determine_category(inputs)
        interval_min, interval_max = IWGDF_FOLLOWUP_INTERVAL_DAYS[category]
        overdue_threshold_days = interval_max if OVERDUE_USES_UPPER_BOUND else interval_min

        overdue: Optional[bool] = None
        days_since_last_eval: Optional[int] = None
        if inputs.last_foot_evaluation_date is not None:
            days_since_last_eval = (inputs.as_of - inputs.last_foot_evaluation_date).days
            overdue = days_since_last_eval > overdue_threshold_days
        else:
            warnings.append("last_foot_evaluation_date 未提供，無法判定是否逾期（overdue 狀態未知，非『未逾期』）")

        result_summary = f"IWGDF Foot Risk = {category}"
        if days_since_last_eval is not None:
            result_summary += f"；last foot evaluation {days_since_last_eval} 天前"
        if overdue:
            result_summary += "；Status = overdue"

        # 是否有機會被低估：LOPS/PAD 至少一項存在、且高風險病史欄位至少
        # 一項未知——此時若那筆未知欄位其實為真，Category 應為 3。
        category_possibly_underestimated = bool(unknown_determinants) and (
            bool(inputs.lops_present) or bool(inputs.pad_present)
        ) and category < 3
        interpretation = f"IWGDF Category {category}"
        if category_possibly_underestimated:
            interpretation += "（★ 未知：ulcer/amputation/kidney_failure 病史未全部評估，實際 Category 可能被低估，見 missing_inputs）"

        if overdue:
            clinical_status = ClinicalStatus.CARE_GAP
            action = f"逾期未評估足部風險，依 Category {category} 建議追蹤頻率 {interval_min}-{interval_max} 天，請安排足部評估"
        elif category in (2, 3):
            clinical_status = ClinicalStatus.HIGH_RISK
            action = f"依 IWGDF Category {category}，建議追蹤頻率 {interval_min}-{interval_max} 天"
        else:
            clinical_status = None
            action = f"依 IWGDF Category {category}，建議追蹤頻率 {interval_min}-{interval_max} 天"
        if category_possibly_underestimated:
            action += "；請補齊 ulcer/amputation/kidney_failure 病史後重新評估，目前 Category 可能低估"

        return CalculatorResult(
            calculator_id=self.calculator_id,
            calculator_version=self.calculator_version,
            tier=self.tier,
            patient_id=inputs.patient_id,
            computed_at=inputs.as_of,
            execution_status=CalculatorExecutionStatus.COMPUTED,
            inputs=input_fields,
            missing_inputs=tuple(unknown_determinants),
            result_values={
                "category": category,
                "interval_days_min": interval_min,
                "interval_days_max": interval_max,
                "overdue": overdue,
                "days_since_last_eval": days_since_last_eval,
            },
            result_summary=result_summary,
            interpretation=interpretation,
            action=action,
            clinical_status=clinical_status,
            guideline=GUIDELINE_ID,
            spec_reference=SPEC_REFERENCE,
            is_placeholder_methodology=False,
            action_grounded_in_spec=True,
            warnings=tuple(warnings),
        )
