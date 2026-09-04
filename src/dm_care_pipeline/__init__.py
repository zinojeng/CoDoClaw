"""
dm_care_pipeline — 糖尿病照護臨床決策支援管線（Part 2）。

九階段：資料整合 → 臨床趨勢 → 併發症辨識 → 風險計算 → Care Gap →
Guideline Recommendation → 醫師決策 → 病人衛教 → 後續追蹤。

架構依據請見 docs/臨床決策支援管線設計.md；本套件只作為
`dm_eligibility`（Part1）的使用者（import），不修改 Part1 任何既有檔案。

對外主要介面：`pipeline.run_stages_1_to_7()` / `pipeline.finalize_pipeline()`。
"""

from .pipeline import PipelineFinalResult, PipelineRunResult, finalize_pipeline, run_stages_1_to_7

__all__ = [
    "run_stages_1_to_7",
    "finalize_pipeline",
    "PipelineRunResult",
    "PipelineFinalResult",
]
