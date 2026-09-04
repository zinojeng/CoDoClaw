# 糖尿病照護臨床決策支援管線 — OpenClaw 擴充架構文件 v2.0

## 0. 本文件與 v1 的關係

v1（`docs/臨床決策支援管線設計.md`）整合了「資料整合｜臨床趨勢｜併發症辨識｜風險計算｜Care Gap｜Guideline Recommendation｜醫師決策｜病人衛教｜後續追蹤」九站的**最小可行骨架**，並已落地為 `src/dm_care_pipeline/` 現有九個模組（`pipeline_models.py` ~ `pipeline.py`）。v1 完全沒有：計算工具庫（Calculator Library）、規格書§30/§31 定義的臨床資料物件、規格書§4/§5 的四態安全設計、Tier B（未在地驗證）模型的介面隔離、Alert 分級、Medication Intelligence Agent、Pre-Visit Brief、完整的 Care-Gap 三時鐘。

本文件（v2）**不是重寫**，而是把六段獨立設計（`clinical_state` / `calculators_tier_a` / `calculators_tier_b` / `complication_guideline` / `medication_care_gap` / `education_followup_alert`）整合進 v1 骨架之上，目標是把「OpenClaw for Diabetes HIS」規格書的 Layer1-9 完整落地為 `src/dm_care_pipeline/` 套件的擴充。

**v1 模組的去留（總覽，細節見第6節）**：

| v1 模組 | v2 處置 |
|---|---|
| `pipeline_models.py` | **修改**：`PatientClinicalProfile` 新增一批 Optional 欄位（向下相容） |
| `data_integration.py` | **修改**：組裝新增的 Layer1 原始資料欄位 |
| `trend_analysis.py` | **不動**：HbA1c/LDL 趨勢與 `QualityMetricTier` 仍是全管線唯一權威來源 |
| `complication_identification.py` | **修改**：碼表擴充（新增 HEART_FAILURE/MASLD_MASH/OBESITY/FOOT_ULCER_HISTORY/AMPUTATION_HISTORY），新增 domain 對照層；既有 `identify_complications()`/`ComplicationFinding`/`ComplicationReport` **保留不變**，作為 Layer2 的輸入之一（不是被取代） |
| `risk.py` | **保留但降級**：`RuleBasedRiskCalculator` 不再是「風險判斷的權威來源」，其輸出改為 Layer2 中 `is_placeholder=True` 的示意性 finding；真正的風險判斷交給 Calculator Library（Tier A/B） |
| `care_gap.py` | **不動**：`assess_care_gaps()`/`CareGapReport` 仍是「這一期還缺什麼（P4P Clock）」的權威來源，被新增的 `care_gap_clocks.py` 包裝、不重算 |
| `guideline_recommendation.py` | **修改**：欄位擴充（guideline_id/version/…/alert_level/related_finding_id），新增 Guideline Library 登錄表；既有 4 條示範規則與 `GuidelineRecommendationEngine` 介面保留 |
| `physician_decision.py` | **修改**：新增 `Reviewable` Protocol 與 `decline_category` 欄位，其餘（PENDING-only 保證、`record_decision()`）完全不動 |
| `education.py` | **修改**：既有 `select_education_topics()` 保留（降級為「延伸資源連結」子元件），新增規格§27 結構化衛教報告產生器 |
| `followup.py` | **修改**：既有 `compute_follow_up_plan()`（P4P 到期日）保留不動，新增規格§28「已開立醫令追蹤」（`PendingOrder`） |
| `pipeline.py` | **修改**：串接新增站點，`run_stages_1_to_7()`/`finalize_pipeline()` 的既有呼叫端行為不變（新參數皆有預設值） |

**新增模組**：`clinical_data_layer.py`、`clinical_data_object.py`、`clinical_state.py`、`calculators/`（package）、`care_gap_clocks.py`、`medication_intelligence.py`、`alert.py`、`pre_visit_brief.py`。

**鐵律延續**（沿用 v1 + 任務指示七條鐵律，本次整合全部保留）：
1. Tier A（規格書逐字公式/切點）照規格書原文實作，不可調整數值。
2. Tier B（僅有工具名稱與變數清單，無完整係數）只做可插拔介面，回傳 `execution_status=REQUIRES_EXTERNAL_VALIDATED_MODEL` + `model_provenance`，不可自行編造係數。
3. 所有計算結果/併發症判斷/guideline 建議一律用規格§30 物件結構，狀態欄位只能是 `confirmed/suspected/high_risk/care_gap` 四值之一。
4. 醫師決策/醫令變更走 Human-in-the-loop，無任何自動核准/自動下醫囑路徑。
5. Guideline 一律走§15 版本化 Guideline Library，每條建議標明來源+版本+規格出處。
6. 無法回溯規格書的假設一律用具名 Config + TODO 顯式標示，不靜默假設。
7. 能重用既有程式（dm_eligibility 規則、v1 既有站點）一律重用，不重複實作。

---

## 1. (a) 完整分層架構與資料流

```
════════════════════════════ dm_eligibility（凍結，只 reuse 不改）════════════════════════════
 HIS/病歷介接 → PatientEnrollmentState ──→ EligibilityEngine.evaluate() → EligibilityReport
════════════════════════════════════════════════════════════════════════════════════
                                    │ state, eligibility_report, physician
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│ Layer 1 — Clinical Data Layer（擴充）                                              │
│  data_integration.py :: build_patient_clinical_profile()                          │
│  clinical_data_layer.py :: VitalSignObservation / OphthalmologyFinding /          │
│    CardiacImagingFinding / FootNeuroExam / VascularExam / ImagingStudyRef /       │
│    HypoglycemiaEventRecord / ProcedureRecord / EncounterUtilizationRecord /       │
│    AdministrativeCareStatus / ClinicalDataSourceRegistry(SourceSystemStatus)      │
│  → PatientClinicalProfile（擴充版，向下相容 v1）  ★ 全管線唯一共同輸入              │
└───────────────────────────────────┬──────────────────────────────────────────────┘
                                    │ profile
        ┌───────────────────────────┼──────────────────────────────────────────┐
        ▼                           ▼                                          ▼
┌───────────────────┐   ┌─────────────────────────┐            ┌──────────────────────────┐
│ trend_analysis.py  │   │ complication_           │            │ care_gap.py（不動）       │
│（不動）             │   │ identification.py       │            │ assess_care_gaps()        │
│ ClinicalTrendReport│   │（碼表擴充）              │            │ → CareGapReport           │
└─────────┬──────────┘   │ → ComplicationReport    │            └─────────────┬─────────────┘
          │              └────────────┬────────────┘                          │
          │                           │                                       │
          │              ┌────────────┴──────────────────────┐                │
          │              ▼                                    ▼                │
          │   ┌────────────────────────┐         ┌──────────────────────────┐  │
          │   │ risk.py（保留，降級為   │         │ calculators/（新增package）│  │
          │   │ illustrative placeholder│        │ Layer 3 — Diabetes         │  │
          │   │ 輸入來源之一）           │        │ Calculator Service         │  │
          │   │ → RiskAssessmentResult  │        │  Tier A（6項，逐字公式）    │  │
          │   └────────────┬─────────────┘        │  Tier B（5項，可插拔介面） │  │
          │                │                       │  → CalculatorResult(s)    │  │
          │                │                       └─────────────┬─────────────┘  │
          └────────────────┴────────────┬───────────────────────┴────────────────┘
                                        ▼
                    ┌──────────────────────────────────────────────────┐
                    │ Layer 2 — Patient Clinical State（新增）           │
                    │ clinical_data_object.py :: ClinicalDomain /        │
                    │   ClinicalStatus / SourceSystem / EvidenceItem /   │
                    │   ModelProvenance / ClinicalFinding                │
                    │ clinical_state.py :: derive_clinical_state()       │
                    │   → PatientClinicalState                           │
                    │   (findings / domain_summaries / TrafficLight)     │
                    │ ★ 規格§4/§5 四態安全設計的具體落地                  │
                    └───────────────────────┬────────────────────────────┘
                                            │ clinical_state
                    ┌────────────────────────┼─────────────────────────────┐
                    ▼                        ▼                             ▼
        ┌────────────────────┐  ┌──────────────────────────┐  ┌──────────────────────────┐
        │ Layer 4 —           │  │ Layer 5 — Guideline Rule │  │ Layer 5' — Medication     │
        │（已併入 Layer2 輸出，│  │ Engine（擴充）            │  │ Intelligence Agent（新增） │
        │ 見 3.5 節裁定）      │  │ guideline_recommendation │  │ medication_intelligence.py│
        │                     │  │ .py                       │  │ → MedicationRecommendation│
        │                     │  │ → GuidelineRecommendation │  │  (實作 Reviewable)         │
        │                     │  │  Report                   │  │                            │
        └─────────────────────┘  └─────────────┬─────────────┘  └─────────────┬──────────────┘
                                                │                              │
                                                └──────────────┬───────────────┘
                                                               ▼
                                    ┌───────────────────────────────────────────┐
                                    │ Layer 6 — 醫師決策（擴充）                  │
                                    │ physician_decision.py :: Reviewable        │
                                    │ present_for_decision(Sequence[Reviewable]) │
                                    │ → PhysicianDecisionRecord（全 PENDING）    │
                                    │ 醫師 UI 逐筆 record_decision()（含          │
                                    │ decline_category：not_applicable/          │
                                    │ contraindicated/other）                    │
                                    │ ★ 鐵律4：唯一決策路徑，無自動核准           │
                                    └─────────────────────┬───────────────────────┘
                                                          │ decision_record.accepted_or_modified()
                        ┌──────────────────────┬───────────┴───────────┬──────────────────────┐
                        ▼                      ▼                       ▼                      ▼
            ┌────────────────────┐  ┌────────────────────┐  ┌──────────────────────┐  ┌────────────────┐
            │ Layer 7 — Alert 分級 │  │ care_gap_clocks.py  │  │ education.py（擴充）  │  │ followup.py     │
            │（新增）alert.py      │  │（新增）Care-Gap Agent│  │ Patient Education     │  │（擴充）Follow-up│
            │ classify_alert_batch│  │ Clinical/P4P/Patient-│  │ Agent §27              │  │ Agent §28        │
            │ → AlertReport        │  │ Specific 三時鐘       │  │ → PatientEducationReport│ │ (PendingOrder    │
            └──────────┬───────────┘  │ → CareGapAgentReport │  └────────────────────────┘ │  追蹤)           │
                       │              └──────────┬───────────┘                             └────────┬─────────┘
                       └──────────────────────────┴──────────────────────┬──────────────────────────┘
                                                                         ▼
                                            ┌───────────────────────────────────────────┐
                                            │ pre_visit_brief.py（新增）                  │
                                            │ generate_pre_visit_brief()                  │
                                            │ → PreVisitDiabetesBrief                     │
                                            │  （Widget 1-6：Today/Trend/ComplicationMap/  │
                                            │   AdvancedRisk/GuidelineGap/Evidence）        │
                                            └───────────────────────┬───────────────────────┘
                                                                    ▼
                                                下一輪 PatientEnrollmentState(as_of_date=...)
                                                → 回到 dm_eligibility 的 EligibilityEngine.evaluate()（封閉迴圈）

未實作、僅留介面（見第7節 MVP 邊界）：
  Population Health Agent（§29）— PopulationHealthAgent(Protocol) 佔位
  Action Layer（§33/Phase4）— ActionLayerGateway(Protocol) 佔位，raise NotImplementedError
```

**資料流原則（延續 v1，新增 2 條）**：
- 每一站輸出獨立、盡量 `frozen` 的報告物件；`PatientClinicalProfile` 仍是唯一「每站都吃」的物件。
- 任何一站不得回頭修改更早期站的報告物件（唯讀輸入）。
- 資料不足一律走 `DataGapFlag` / `warnings` / `SourceSystemStatus.NOT_INTEGRATED` / `TrafficLight.GRAY`，不得靜默假設「沒查=沒事」。
- **新增**：Layer2 `PatientClinicalState` 是 Layer4 起（Guideline/Medication/Care-Gap/Alert/Education/Pre-Visit Brief）唯一應該消費的「病人臨床事實來源」，不得各自重新從 `ComplicationReport`/`RiskAssessmentResult`/`CalculatorResult` 組裝一份自己的臨床狀態判斷。
- **新增**：Tier B 計算工具的結果永遠不得以「已驗證的高風險數字」形式出現在任何 UI 欄位；`execution_status != COMPUTED` 時，`result_values` 恆為 `None`。

---

## 2. 命名統一總表（六段設計 → 最終命名，附裁定理由）

| 概念 | 原始命名（六段設計分歧） | 最終統一命名 | 裁定理由 |
|---|---|---|---|
| §30 臨床資料物件 | `OpenClawClinicalDataObject`（complication_guideline / medication_care_gap / education_followup_alert）vs `ClinicalFinding`（clinical_state） | **`ClinicalFinding`**（`clinical_data_object.py`） | `clinical_state` 的欄位設計最完整（含 `finding_id` 供 Layer5/7 以 `dataclasses.replace()` 產生衍生版本、`model_provenance`、`is_placeholder`），且直接呼應規格§30「換 AI model 時 clinical logic 不需要全部重做」對 stable id 的隱含需求 |
| §5 四態列舉 | `ClinicalStatus`（clinical_state / complication_guideline 已用同名）vs `ClinicalDataObjectStatus`（education_followup_alert）vs inline `Literal[...]`（medication_care_gap） | **`ClinicalStatus(str, Enum)`**（`clinical_data_object.py`） | 兩段設計已巧合收斂到同一名稱；改用 Enum 而非 Literal，型別安全且與 v1 既有列舉風格一致 |
| Calculator 執行狀態 vs 臨床狀態 | `calculators_tier_a` 把 §5 四值直接叫 `CalculatorStatus`；`calculators_tier_b` 把「COMPUTED/INSUFFICIENT_DATA/NOT_APPLICABLE/REQUIRES_EXTERNAL_VALIDATED_MODEL」也叫 `CalculatorStatus`（同名不同義，直接衝突） | **`CalculatorExecutionStatus`**（執行狀態，4值，`calculators/base.py`）與 **`ClinicalStatus`**（臨床狀態，§5四值，`clinical_data_object.py`，`CalculatorResult.clinical_status` 欄位消費它）分離成兩個型別 | 這正是 `calculators_tier_a` 自己在 open_questions 提出的「鐵律2 vs 鐵律3 文字衝突」，本文件裁定：完全分離「計算工具有沒有算出來」與「算出來後對應哪個臨床狀態」，兩者是正交的兩件事 |
| Calculator 結果物件 | `CalculatorResult`（三段各自宣告不同欄位：`calculators_tier_a`/`calculators_tier_b`/`complication_guideline` 的 `calculator_contracts.py`/`education_followup_alert` 的 `clinical_data_object.py` 版本） | **`CalculatorResult`**（唯一定義於 `calculators/base.py`），`calculator_contracts.py`（complication_guideline 提議）與 `education_followup_alert` 自訂版本**取消**，改 import 本檔案 | Calculator Library 是這個物件的實際擁有者（Layer3），其餘站點應該消費而非各自假設；欄位合併見第3.4節 |
| Model Provenance | `calculators_tier_a` 的 `ModelProvenance`（原始population/在地驗證bool/驗證說明）vs `calculators_tier_b` 的 `ModelProvenance`（model_name/derivation_population/驗證狀態三態/spec_reference/warning） | **`ModelProvenance`**（`clinical_data_object.py`，取 `calculators_tier_b` 的三態 `taiwan_local_validation_status` + `calculators_tier_a` 的必填不可空字串驗證精神），`calculators/base.py` 直接 import 不重新定義 | 三態（`not_locally_validated`/`local_calibration_in_progress`/`locally_validated`）比純 bool 更能表達未來「正在做在地驗證研究」的過渡狀態 |
| 併發症→疾病領域對照 | `clinical_state` 的 `ClinicalDomain` enum + `COMPLICATION_CATEGORY_TO_DOMAIN`；`complication_guideline` 的 `COMPLICATION_DOMAINS: dict[str, ComplicationDomainMeta]`（9條件、3大群組、`display_groups`） | **合併**：`ClinicalDomain`（`clinical_data_object.py`，13值）為權威列舉；`COMPLICATION_CATEGORY_TO_DOMAIN`（`complication_identification.py`）做 ICD 類別鍵→`ClinicalDomain` 映射；`DOMAIN_DISPLAY_GROUPS`（`clinical_data_object.py`）保留 `complication_guideline` 的 3 大群組 UI 分組與 KIDNEY 雙重歸屬設計 | 兩段設計本質是同一件事的兩個層次（內部狀態機列舉 vs UI 分組 metadata），合併不衝突；KIDNEY 同時屬於 microvascular 與 cardiometabolic 兩組是規格§14 原文本身的設計（ADA CKM 框架），非工程重複 |
| Layer2/Layer4「病人臨床狀態」核心組裝邏輯 | `clinical_state.derive_clinical_state()`（→`PatientClinicalState`）vs `complication_guideline` 的 `complication_identification.detect_complications()`（→`ComplicationDetectionReport`/`ComplicationDomainFinding`/`PendingValidationNotice`） | **`clinical_state.derive_clinical_state()`** 為唯一權威 | 兩者職責完全重疊（同樣消費 ComplicationReport/CareGapReport/RiskAssessmentResult/CalculatorResult，同樣要判斷 confirmed/suspected/high_risk/care_gap）。`clinical_state` 版本多做到兩件 `complication_guideline` 沒做的安全設計：① `domain_summaries` 保證每個 domain 都有輸出（即使 GRAY，避免「沒資料=沒顯示=看起來沒事」）；② `TrafficLight.GRAY` 明確綁定 `SourceSystemStatus.NOT_INTEGRATED`（防止「沒接系統」被誤讀成「沒有病」）。`complication_guideline` 的 `PendingValidationNotice`（Tier B 唯一線索時獨立於 findings 外回報）**併入** `clinical_state` 既有設計：Tier B 結果一律轉為 `status=CARE_GAP, severity="pending_local_validation", is_placeholder=True` 的 `ClinicalFinding`，不再開一個平行清單（原因見第8節裁定記錄#3） |
| 併發症狀態判斷可插拔規則 | `complication_guideline` 的 `ComplicationStatusResolver`(Protocol) + `DefaultComplicationStatusResolver` | **保留精神，改名 `ClinicalStatusResolver`**，作為 `derive_clinical_state()` 內部可選插拔點（`clinical_state.py`） | 可插拔規則的設計價值值得保留，但物件形狀改為輸出 `ClinicalFinding` 而非 `ComplicationDomainFinding`，配合上一條裁定 |
| Guideline 建議與 finding 的關聯欄位 | `complication_guideline` 的 `RecommendationRule.related_condition: Optional[str]`（字串比對 condition） | **`related_finding_id: Optional[str]`**（`guideline_recommendation.py`，指向 `ClinicalFinding.finding_id`） | `clinical_state` 已提供穩定 `finding_id` 作外鍵，字串比對 condition 容易因大小寫/命名不一致而失聯，改用 id 外鍵更穩健 |
| §30 物件最終合成（含 guideline/action_status） | `complication_guideline` 的 `build_clinical_data_objects()`；`education_followup_alert` 的四個 adapter 函式（`complication_finding_to_cdo`/`care_gap_item_to_cdo`/`calculator_result_to_cdo`/`guideline_recommendation_to_cdo`） | **`compose_clinical_data_objects()`**（`clinical_data_object.py`），以 `dataclasses.replace()` 對 `clinical_state.findings` 中的 base `ClinicalFinding` 逐筆疊加 guideline/recommendation/action_status/clinician_response，取代兩段設計各自的 adapter | `clinical_state` 已把「finding 本身」在 Layer2 就建好，Layer5/6 不需要重新從零轉譯 ComplicationFinding/CareGapItem/CalculatorResult，只需要在既有 finding 上疊加派生欄位——這正是規格§30「換 model 不需重做」的精神，也是 `clinical_state` 自己在 key_interfaces 提出的建議 |
| 醫師決策可泛化介面 | `medication_care_gap` 的 `Reviewable` Protocol | **採納**（`physician_decision.py`） | 讓 `GuidelineRecommendation` 與 `MedicationRecommendation` 都能流入同一個 `present_for_decision()`，非破壞性擴充（參數型別從具體型別放寬為 `Sequence[Reviewable]`） |
| 醫師「Not applicable/Contraindicated」語意缺口 | 多段設計皆獨立發現 `PhysicianDecisionStatus` 只有 4 值、不含規格§25 的 5 按鈕語意 | **`PhysicianDecision.decline_category: Optional[Literal["not_applicable","contraindicated","other"]]`**（`medication_care_gap` 提案採納） | 最小、非破壞性；`DECLINED` 狀態不變，只是多一個分類欄位，UI 的 [Not applicable]/[Contraindicated]/[Dismiss] 三個按鈕都映射到 `DECLINED` + 不同 `decline_category` |
| Alert 分級 | 僅 `education_followup_alert` 設計；`complication_guideline` 有 `alert_level` 欄位但明確定位「保留給未來 Medication Agent，本層不產生 safety_alert」 | **採納 `education_followup_alert` 的 `alert.py`**（`AlertLevel`/`classify_alert()`/`AlertReport`），輸入改為消費 `ClinicalFinding`（而非該段原設計消費的 `ClinicalDataObject`），`complication_guideline` 的 `alert_level` 欄位改為 `GuidelineRecommendation` 上的**建議性** hint（非強制，最終分級仍由 `alert.py` 統一計算） | 分級規則若同時存在於 `GuidelineRecommendation.alert_level` 與獨立的 `classify_alert()`，會有「兩處各自判斷同一件事」的風險，改為單一權威計算點 |
| IWGDF 分級→追蹤頻率對照表 | `complication_guideline` 提議放 `complication_identification.py`；`medication_care_gap` 的 `care_gap_clocks.py` 也需要同一份對照 | **`calculators/iwgdf_foot.py`**（IWGDF 計算工具自己的模組層級常數 `IWGDF_FOLLOWUP_INTERVAL_DAYS`），`care_gap_clocks.py`/其餘模組一律 import，不重複宣告 | 這份對照表是 IWGDF calculator 輸出語意的一部分（category→頻率是規格§10 同一段逐字定義），放在 calculator 自己的模組最貼近「唯一權威來源」原則 |
| Order 追蹤 vs 三時鐘 Care Gap | `education_followup_alert` 的 `followup.py` 擴充（`PendingOrder`，§28「已開醫令是否完成」）vs `medication_care_gap` 的 `care_gap_clocks.py`（§18「三時鐘」，「應該做什麼」） | **兩者都保留，是不同概念**：`followup.py` 追蹤「已開立醫令的完成度」；`care_gap_clocks.py` 判斷「依臨床/P4P/病人專屬嚴重度，這次該不該做」 | 規格書§18 與§28 本身就是兩個不同章節/不同 Agent（Care-Gap Agent vs Follow-up Agent），無需合併 |
| 套件路徑/既有九站命名 | 六段設計一致沿用 `src/dm_care_pipeline/` 與 v1 站點命名 | 不變 | 六段設計皆已知悉 v1 骨架存在，無分歧 |

---

## 3. (b) 各模組最終簽名

### 3.0 命名風格延續

沿用 v1 3.0 節裁定：所有新增的「封閉受控詞彙」一律 `class X(str, Enum)`；ICD 類別鍵、照護碼、calculator_id 等「外部代碼表 key」維持裸字串。

---

### 3.1 Layer 1 擴充 — `clinical_data_layer.py`（新增）

```python
# src/dm_care_pipeline/clinical_data_layer.py
"""規格§3 Layer1 的 dm_care_pipeline 擴充：既有 dm_eligibility.models 未涵蓋的原始臨床
資料容器。全部 frozen；本檔案不做任何臨床判讀，只做資料到位與否的顯性標記。
"""

class SourceSystemStatus(str, Enum):
    NOT_INTEGRATED = "not_integrated"        # 系統尚未介接，禁止推斷任何陰性/正常結論
    INTEGRATED_NO_DATA = "integrated_no_data" # 已查詢、確認無資料（可視為「確認陰性」的必要條件之一）
    INTEGRATED_HAS_DATA = "integrated_has_data"

@dataclass(frozen=True)
class ClinicalDataSourceRegistry:
    his_vitals: SourceSystemStatus = SourceSystemStatus.NOT_INTEGRATED
    lis_extended: SourceSystemStatus = SourceSystemStatus.NOT_INTEGRATED   # AST/ALT/Platelet/BNP/NT-proBNP 等擴充LIS項目
    cvis: SourceSystemStatus = SourceSystemStatus.NOT_INTEGRATED
    pacs: SourceSystemStatus = SourceSystemStatus.NOT_INTEGRATED
    ophthalmology: SourceSystemStatus = SourceSystemStatus.NOT_INTEGRATED
    foot_neuro: SourceSystemStatus = SourceSystemStatus.NOT_INTEGRATED
    vascular_lab: SourceSystemStatus = SourceSystemStatus.NOT_INTEGRATED
    admin: SourceSystemStatus = SourceSystemStatus.NOT_INTEGRATED
    last_queried_at: dict[str, date] = field(default_factory=dict)  # 系統名→最後查詢日

class SmokingStatus(str, Enum):
    NEVER = "never"; FORMER = "former"; CURRENT = "current"; UNKNOWN = "unknown"

@dataclass(frozen=True)
class VitalSignObservation:
    observation_date: date
    systolic_bp: Optional[float] = None
    diastolic_bp: Optional[float] = None
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    bmi: Optional[float] = None                # 可由 height/weight 換算，或 HIS 直接提供
    smoking_status: SmokingStatus = SmokingStatus.UNKNOWN
    source: str = "HIS"

@dataclass(frozen=True)
class OphthalmologyFinding:
    exam_date: date
    method: Literal["manual", "VeriSee_AI", "other"]
    dr_classification: Literal["none", "mild_npdr", "moderate_npdr", "severe_npdr", "pdr", "ungradable"]
    dme_present: Optional[bool] = None
    laterality: Optional[Literal["OD", "OS", "OU"]] = None
    diagnosis_text: str = ""
    source: str = "OPHTHALMOLOGY"

@dataclass(frozen=True)
class ProcedureRecord:
    procedure_code: Optional[str]      # 允許為空、自由文字（非結構化醫令系統過渡容器）
    procedure_name: str
    procedure_date: date
    source: str

@dataclass(frozen=True)
class CardiacImagingFinding:
    study_date: date
    modality: Literal["ECG", "ECHO"]
    qrs_duration_ms: Optional[float] = None
    lvef_percent: Optional[float] = None
    diastolic_dysfunction_grade: Optional[str] = None
    structural_heart_disease_present: Optional[bool] = None
    source: str = "CVIS"

@dataclass(frozen=True)
class UlcerRecord:
    event_date: date
    resolved: bool
    laterality: Optional[Literal["L", "R", "bilateral"]] = None
    source: str = "FOOT_NEURO"

@dataclass(frozen=True)
class AmputationRecord:
    event_date: date
    laterality: Optional[Literal["L", "R", "bilateral"]] = None
    level: Optional[str] = None
    source: str = "FOOT_NEURO"

@dataclass(frozen=True)
class FootNeuroExam:
    exam_date: date
    monofilament_result_left: Literal["normal", "abnormal", "not_tested"] = "not_tested"
    monofilament_result_right: Literal["normal", "abnormal", "not_tested"] = "not_tested"
    vibration_result: Literal["normal", "abnormal", "not_tested"] = "not_tested"
    temperature_pinprick_result: Literal["normal", "abnormal", "not_tested"] = "not_tested"
    ncv_result: Optional[str] = None
    foot_deformity_present: Optional[bool] = None   # 含 Charcot foot
    ulcer_history: tuple[UlcerRecord, ...] = ()
    amputation_history: tuple[AmputationRecord, ...] = ()
    source: str = "FOOT_NEURO"
    # LOPS（loss of protective sensation）判定邏輯留給 Layer3 IWGDF calculator，
    # 本類別只提供原始欄位（鐵律7：資料容器與判讀邏輯分離）。

@dataclass(frozen=True)
class VascularExam:
    exam_date: date
    abi_right: Optional[float] = None
    abi_left: Optional[float] = None
    tbi_right: Optional[float] = None
    tbi_left: Optional[float] = None
    doppler_summary: Optional[str] = None
    angiography_summary: Optional[str] = None
    claudication_present: Optional[bool] = None
    pedal_pulse_present: Optional[bool] = None
    revascularization_history: tuple[ProcedureRecord, ...] = ()
    source: str = "VASCULAR_LAB"

@dataclass(frozen=True)
class ImagingStudyRef:
    study_date: date
    modality: str
    body_region: Literal["liver", "cardiac", "vascular", "other"]
    report_text: str = ""
    structured_findings: dict = field(default_factory=dict)
    source: str = "PACS"

@dataclass(frozen=True)
class HypoglycemiaEventRecord:
    event_date: date
    severity: Literal["level1", "level2", "level3"]
    setting: Literal["self_reported", "outpatient", "ed", "inpatient"]
    source: str = "HIS"

@dataclass(frozen=True)
class ReferralRecord:
    specialty: str
    ordered_date: date
    status: Literal["ordered", "completed", "cancelled"]

@dataclass(frozen=True)
class AdministrativeCareStatus:
    diabetes_shared_care_enrolled: Optional[bool] = None
    upcoming_appointment_date: Optional[date] = None
    pending_referrals: tuple[ReferralRecord, ...] = ()
    diabetes_educator_involved: Optional[bool] = None
    dietitian_involved: Optional[bool] = None
    source: str = "ADMIN"
    # ckd_p4p_enrolled 刻意不重複維護：改由呼叫端讀
    # profile.eligibility_report.eligible_codes() 推導（dm_eligibility EligibilityReport
    # 已是收案狀態的權威來源，見架構文件 §5 節整合原則）。

@dataclass(frozen=True)
class EncounterUtilizationRecord:
    """★ 新增、平行於 dm_eligibility `Encounter`（凍結，不修改）的「就醫場域分類」
    容器，供 Karter Hypoglycemia Tier B calculator 之
    ed_visits_prior_12mo/prior_hypo_related_ed_or_hosp 使用。dm_eligibility
    `Encounter` 本身沒有門診/急診/住院分類欄位，這是唯讀的平行擴充，不改動
    dm_eligibility 既有物件（見第4/5節 open_questions）。"""
    encounter_id: str  # 建議與 dm_eligibility Encounter.encounter_id 對應，供交叉核對
    visit_date: date
    setting: Literal["outpatient", "ed", "inpatient"]
    hypoglycemia_related: Optional[bool] = None
    source: str = "HIS"
```

**與 `pipeline_models.PatientClinicalProfile` 的關係（提案，需與 §5 節「協調事項」確認落地owner）**：新增下列 Optional 欄位（皆預設空 tuple/None，向下相容，不動既有欄位）：

```python
    vital_signs: tuple[VitalSignObservation, ...] = ()
    ophthalmology_findings: tuple[OphthalmologyFinding, ...] = ()
    cardiac_imaging: tuple[CardiacImagingFinding, ...] = ()
    foot_neuro_exams: tuple[FootNeuroExam, ...] = ()
    vascular_exams: tuple[VascularExam, ...] = ()
    imaging_studies: tuple[ImagingStudyRef, ...] = ()
    hypoglycemia_events: tuple[HypoglycemiaEventRecord, ...] = ()
    procedures: tuple[ProcedureRecord, ...] = ()
    encounter_utilization: tuple[EncounterUtilizationRecord, ...] = ()
    administrative_status: Optional[AdministrativeCareStatus] = None
    data_source_registry: ClinicalDataSourceRegistry = field(default_factory=ClinicalDataSourceRegistry)
    sex: Optional[Literal["male", "female", "intersex_unspecified"]] = None
```

`sex` 是本次整合唯一一個**不屬於 `clinical_data_layer.py` 型別、直接掛在 `PatientClinicalProfile` 上的新原始欄位**（PREVENT/Legacy ASCVD PCE/KFRE 皆需要，dm_eligibility/dm_care_pipeline 目前完全沒有這個欄位）；其定義（生理性別 vs 病歷登記性別）本身是待人工裁定事項，見第4節 open_questions。

`data_integration.py` 的對應修改：`build_patient_clinical_profile()` 新增對應的 keyword-only 參數（皆預設空/None），維持既有呼叫端零改動即可運作；新增資料缺口比照既有 `DataGapFlag` 模式（例如 `data_source_registry` 全部 `NOT_INTEGRATED` 時，記一筆 `DataGapFlag(source="clinical_data_layer", status="missing", relevant_downstream_stages=("clinical_state","calculators",...))`）。

---

### 3.2 §30/§31 共用型別 — `clinical_data_object.py`（新增）

全管線唯一權威來源；其餘檔案一律 import，不得自建同義型別。

```python
# src/dm_care_pipeline/clinical_data_object.py

class ClinicalDomain(str, Enum):
    GLYCEMIC_CONTROL = "GLYCEMIC_CONTROL"
    KIDNEY = "KIDNEY"
    EYE = "EYE"
    NEUROPATHY = "NEUROPATHY"
    FOOT = "FOOT"
    HEART_FAILURE = "HEART_FAILURE"
    ASCVD = "ASCVD"
    CEREBROVASCULAR = "CEREBROVASCULAR"
    PAD = "PAD"
    LIVER = "LIVER"
    HYPOGLYCEMIA = "HYPOGLYCEMIA"
    BLOOD_PRESSURE = "BLOOD_PRESSURE"
    WEIGHT_OBESITY = "WEIGHT_OBESITY"

# 規格§14 Microvascular/Macrovascular/Cardiometabolic 三大群組的 UI 分組
# metadata。KIDNEY 刻意同時屬於兩組（ADA CKM 框架原文設計，非工程重複，
# 見第2節命名統一總表裁定）。純資訊揭露用途，不影響 finding 產生邏輯。
DOMAIN_DISPLAY_GROUPS: dict[ClinicalDomain, tuple[str, ...]] = {
    ClinicalDomain.KIDNEY: ("microvascular", "cardiometabolic"),
    ClinicalDomain.EYE: ("microvascular",),
    ClinicalDomain.NEUROPATHY: ("microvascular",),
    ClinicalDomain.FOOT: ("microvascular",),
    ClinicalDomain.ASCVD: ("macrovascular",),
    ClinicalDomain.CEREBROVASCULAR: ("macrovascular",),
    ClinicalDomain.PAD: ("macrovascular",),
    ClinicalDomain.HEART_FAILURE: ("cardiometabolic",),
    ClinicalDomain.LIVER: ("cardiometabolic",),
    ClinicalDomain.WEIGHT_OBESITY: ("cardiometabolic",),
    ClinicalDomain.GLYCEMIC_CONTROL: (),   # 品質指標型 domain，不進複雜度地圖三分組
    ClinicalDomain.HYPOGLYCEMIA: (),
    ClinicalDomain.BLOOD_PRESSURE: (),
}

class ClinicalStatus(str, Enum):
    """★★★ 規格§5 四態，全管線唯一權威定義，逐字對應規格四種安全分級 ★★★"""
    CONFIRMED = "confirmed"
    SUSPECTED = "suspected"
    HIGH_RISK = "high_risk"
    CARE_GAP = "care_gap"

class SourceSystem(str, Enum):
    HIS = "HIS"; LIS = "LIS"; CPOE = "CPOE"; CVIS = "CVIS"; PACS = "PACS"
    OPHTHALMOLOGY = "OPHTHALMOLOGY"; FOOT_NEURO = "FOOT_NEURO"
    VASCULAR_LAB = "VASCULAR_LAB"; ADMIN = "ADMIN"
    CALCULATOR = "CALCULATOR"; DERIVED = "DERIVED"

@dataclass(frozen=True)
class EvidenceItem:
    label: str
    value: str              # 統一字串化以容納數值/類別型結果（規格§26 Evidence Widget逐筆列點需求）
    unit: Optional[str] = None
    observed_date: Optional[date] = None
    source: SourceSystem = SourceSystem.DERIVED

LOCAL_VALIDATION_WARNING = (
    "此模型並非以台灣族群建立，適合 risk communication / risk stratification，"
    "台灣正式進入 decision threshold 前應完成 local calibration / validation"
    "（OpenClaw for Diabetes HIS §37）"
)

@dataclass(frozen=True)
class ModelProvenance:
    """Tier B calculator 必填；`clinical_data_object`/`calculators` 兩層共用
    唯一定義（calculators/base.py 直接 import，不重新宣告）。"""
    model_name: str                                    # 例如 "WATCH-DM Score (Segar et al.)"
    original_population: str                           # 不可空字串，例如 "T2DM 衍生/驗證世代（美國多中心）"
    taiwan_local_validation_status: Literal[
        "not_locally_validated", "local_calibration_in_progress", "locally_validated"
    ] = "not_locally_validated"
    spec_reference: str = ""                            # 例如 "OpenClaw for Diabetes HIS.md §6.3, §37"
    warning: str = LOCAL_VALIDATION_WARNING

    @property
    def locally_validated(self) -> bool:
        return self.taiwan_local_validation_status == "locally_validated"

@dataclass(frozen=True)
class ClinicalFinding:
    """★★★ 規格§30 OpenClaw Clinical Data Object，全管線唯一權威型別 ★★★
    Layer2（clinical_state.derive_clinical_state()）建立時只保證填好
    finding_id/patient_id/domain/condition/status/severity/evidence/source/
    date/calculator/calculator_version/model_provenance/is_placeholder；
    guideline/recommendation/action_status/clinician_response 留給 Layer5/6
    透過 `compose_clinical_data_objects()`（見下）以 dataclasses.replace()
    產生衍生版本填入（frozen，禁止原地 mutate，呼應規格§36 audit trail
    「保留每一版判斷」精神）。"""
    finding_id: str                 # 穩定id，供Layer5/6/Audit Trail引用；建議格式 f"{domain}:{condition}:{patient_id}:{date}"
    patient_id: str
    domain: ClinicalDomain
    condition: str                  # 人類可讀病名，例如 "CKD" / "糖尿病足"
    status: ClinicalStatus
    severity: Optional[str] = None
    evidence: tuple[EvidenceItem, ...] = ()
    source: SourceSystem = SourceSystem.DERIVED
    date: date = None               # type: ignore[assignment]  # 必填，dataclass 預設值僅為型別工具妥協
    calculator: Optional[str] = None
    calculator_version: Optional[str] = None
    guideline: Optional[str] = None
    recommendation: Optional[str] = None
    action_status: str = "not_yet_reviewed"
    clinician_response: Optional[str] = None
    model_provenance: Optional[ModelProvenance] = None
    is_placeholder: bool = False
    generated_at: datetime = None   # type: ignore[assignment]  # 必填，由建構端一律傳入 datetime.now()


def compose_clinical_data_objects(
    state: "PatientClinicalState",
    guideline_report: "GuidelineRecommendationReport",
    decision_record: "PhysicianDecisionRecord | None" = None,
) -> list[ClinicalFinding]:
    """把 Layer2 base findings 與 Layer5 guideline 建議、Layer6 醫師決策
    「合成」為最終供 UI/Audit 使用的完整 §30 物件清單（以 finding_id 為
    join key，dataclasses.replace() 產生新版本，不 mutate 原 finding）。
    沒有對應 GuidelineRecommendation 命中的 finding 原樣輸出（guideline/
    recommendation 維持 None，action_status 維持 "not_yet_reviewed"）；
    一個 finding 若被多條規則命中，取 priority 最高者。"""
```

---

### 3.3 Layer 2 — `clinical_state.py`（新增）

```python
# src/dm_care_pipeline/clinical_state.py

class TrafficLight(str, Enum):
    GREEN = "GREEN"; YELLOW = "YELLOW"; RED = "RED"; GRAY = "GRAY"
    # GRAY 專屬對應 SourceSystemStatus.NOT_INTEGRATED——未介接系統絕不可
    # 顯示綠燈（不可把「沒查」誤讀成「沒事」，鐵律6的核心落地）。

@dataclass(frozen=True)
class DomainSummary:
    domain: ClinicalDomain
    traffic_light: TrafficLight
    headline: str                       # 例如 "CKD G3aA2" / "No DR documented, screened, negative"
    finding_ids: tuple[str, ...] = ()   # 可為空——「確認陰性篩檢」情境見下方設計決定
    last_updated: Optional[date] = None

@dataclass
class PatientClinicalState:
    patient_id: str
    as_of_date: date
    findings: tuple[ClinicalFinding, ...]
    domain_summaries: dict[ClinicalDomain, DomainSummary]
    data_gaps: list["DataGapFlag"]      # 沿用 pipeline_models 既有型別
    warnings: list[str] = field(default_factory=list)

    def confirmed(self) -> tuple[ClinicalFinding, ...]: ...
    def suspected(self) -> tuple[ClinicalFinding, ...]: ...
    def high_risk(self) -> tuple[ClinicalFinding, ...]: ...
    def care_gaps(self) -> tuple[ClinicalFinding, ...]: ...
    def by_domain(self, domain: ClinicalDomain) -> tuple[ClinicalFinding, ...]: ...
    def get(self, finding_id: str) -> Optional[ClinicalFinding]: ...


class ClinicalStatusResolver(Protocol):
    """可插拔的 domain 狀態判斷規則（承接 complication_guideline 設計精神，
    輸出型別改為 ClinicalFinding，見第2節命名統一裁定）。"""
    def resolve(
        self, domain: ClinicalDomain, profile: "PatientClinicalProfile",
        complication_report: "ComplicationReport", care_gap_report: "CareGapReport",
        calculator_results: Mapping[str, "CalculatorResult"],
    ) -> tuple[ClinicalFinding, ...]: ...


@dataclass
class ClinicalStateConfig:
    """★ 工程規則化詮釋的具名旗標集合，非規格書逐字給出的判定演算法；
    正式上線前需臨床覆核（比照 v1 EligibilityConfig 風格）。"""
    tier_a_confirmed_requires_icd_corroboration: bool = True   # open_questions#3
    placeholder_risk_finding_domain: ClinicalDomain = ClinicalDomain.GLYCEMIC_CONTROL  # RuleBasedRiskCalculator 產生的 is_placeholder finding 掛哪個 domain（工程佔位）


def derive_clinical_state(
    profile: "PatientClinicalProfile",
    complication_report: "ComplicationReport",
    care_gap_report: "CareGapReport",
    risk_result: "RiskAssessmentResult",
    calculator_results: Mapping[str, "CalculatorResult"] = (),
    resolver: Optional[ClinicalStatusResolver] = None,
    config: Optional[ClinicalStateConfig] = None,
) -> PatientClinicalState:
    """純函式，reuse 既有 Layer3/4/5 報告物件（不重算）：
    1. 併發症(ComplicationReport.findings) → ClinicalFinding，status=CONFIRMED，
       domain 由 COMPLICATION_CATEGORY_TO_DOMAIN 映射；NEPHROPATHY 類另把
       ComplicationFinding.ckd_stage 附進 severity（明確標註：此為 dm_eligibility
       CKDAssessment.stage() 的 P4P 收案分期子集，不等於規格§6.1完整
       KDIGO G1-G5/A1-A3，見第4節open_questions#2）。
    2. Care Gap(CareGapReport.deduplicated_missing_items) → ClinicalFinding，
       status=CARE_GAP，domain 依 checklist 項目對應（如 NMRP/眼底→EYE）。
    3. calculator_results 中 execution_status==COMPUTED 且
       clinical_status 非 None 者 → 直接採用該 clinical_status。
    4. calculator_results 中 execution_status==REQUIRES_EXTERNAL_VALIDATED_MODEL
       （Tier B）→ status=CARE_GAP, severity="pending_local_validation",
       is_placeholder=True, model_provenance 原樣帶入，condition 文字明確
       標註「本地驗證前不可作為風險分級依據」（鐵律2的資料模型層落實）。
    5. risk.RuleBasedRiskCalculator 的 contributions → 全部轉成
       is_placeholder=True 的 finding，condition 文字強制帶「非已驗證公式」
       字樣。
    6. 逐 domain 彙總成 domain_summaries：有 RED/YELLOW finding → 對應燈號；
       ClinicalDataSourceRegistry 該 domain 對應系統為 NOT_INTEGRATED →
       GRAY（優先權高於「查無finding」）；否則若有
       INTEGRATED_HAS_DATA 且無異常 finding → GREEN。
    """
```

**設計決定（延續 clinical_state 原始設計，本文件採納）**：「確認陰性篩檢」（如 `"No DR documented, screened, negative"`）不進入 `findings`，只放進 `domain_summaries`（綠燈+headline文字）。若某 domain 當次沒有 `ClinicalFinding`，`domain_summaries` 仍必須存在一筆（哪怕只是 GRAY「尚未介接/未評估」），確保 UI 永遠有明確狀態可顯示。

`COMPLICATION_CATEGORY_TO_DOMAIN` 定義於 `complication_identification.py`（見 3.5 節），非 `clinical_state.py`——因為它是 `COMPLICATION_ICD10_PREFIXES` key 的映射，鍵的擁有者是併發症辨識站，`clinical_state.py` 只 import 使用（鐵律7）。

---

### 3.4 Calculator Library — `calculators/`（新增 package）

規格§13「未來增加 calculator 只增加一個 Calculator Module」的具體落地；不放進 `complication_identification.py`/`risk.py`，因 calculator 需獨立版控（§35 `calculator/KDIGO_GA/v1`）、獨立單元測試。

```
calculators/
├── __init__.py            # re-export
├── base.py                # 共用契約（本節）
├── registry.py            # CalculatorRegistry
├── ckd_ga.py               } Tier A（6項）
├── fib4.py                  }
├── bnp_hf_screen.py         }
├── abi_tbi.py                }
├── iwgdf_foot.py              }
├── hypoglycemia_ada_l1.py      }
└── tier_b/
    ├── __init__.py         # register_tier_b_calculators()
    ├── _base.py            # TierBCalculatorBase
    ├── watch_dm.py
    ├── prevent_ascvd.py    # PreventCalculator, LegacyAscvdPceCalculator, already_in_secondary_prevention()
    ├── karter_hypoglycemia.py
    └── kfre.py
```

#### `calculators/base.py`

```python
from ..clinical_data_object import ClinicalStatus, ModelProvenance, LOCAL_VALIDATION_WARNING  # 唯一來源，不重新定義

class CalculatorTier(str, Enum):
    A = "A"   # 規格書逐字公式/切點，可計算
    B = "B"   # 僅工具名稱+變數清單，無完整係數，僅可插拔

class CalculatorExecutionStatus(str, Enum):
    """★ 計算工具『有沒有算出來』的執行狀態，與 ClinicalStatus（算出來後
    對應哪個臨床狀態）完全分離——這是對鐵律2/鐵律3文字衝突的裁定（見第2節
    命名統一總表）。"""
    COMPUTED = "computed"
    INSUFFICIENT_DATA = "insufficient_data"
    NOT_APPLICABLE = "not_applicable"                          # 例如 PREVENT 年齡不在30-79歲/已進入secondary prevention
    REQUIRES_EXTERNAL_VALIDATED_MODEL = "requires_external_validated_model"  # Tier B 固定回傳

@dataclass(frozen=True)
class CalculatorInputField:
    name: str
    provided: bool
    value: Optional[object] = None
    source: Optional[str] = None            # 例如 lab item_code 或 profile 欄位路徑
    observed_date: Optional[date] = None

@dataclass(frozen=True)
class CalculatorResult:
    calculator_id: str                       # 例如 "KDIGO_GA"（比照§35 calculator/KDIGO_GA/v1）
    calculator_version: str                  # 例如 "v1.0"
    tier: CalculatorTier
    patient_id: str
    computed_at: date
    execution_status: CalculatorExecutionStatus
    inputs: tuple[CalculatorInputField, ...] = ()
    missing_inputs: tuple[str, ...] = ()     # required_inputs 中缺漏者
    result_values: Optional[dict[str, object]] = None   # execution_status!=COMPUTED 時恆為 None（鐵律2的程式化落實）
    result_summary: Optional[str] = None     # 人類可讀，例如 "CKD G3aA2"
    interpretation: Optional[str] = None     # Tier B 恆 None（不得生成"High risk"等文字冒充已驗證判讀）
    action: Optional[str] = None
    clinical_status: Optional[ClinicalStatus] = None   # 規格§5四值，僅 execution_status==COMPUTED 時可能非None
    guideline: Optional[str] = None
    spec_reference: str = ""
    is_placeholder_methodology: bool = False  # Tier A 恆 False；Tier B 恆 True
    action_grounded_in_spec: bool = False
    model_provenance: Optional[ModelProvenance] = None   # Tier B 必填
    warnings: tuple[str, ...] = ()

class Calculator(Protocol):
    calculator_id: ClassVar[str]
    calculator_version: ClassVar[str]
    tier: ClassVar[CalculatorTier]
    required_inputs: ClassVar[tuple[str, ...]]
    def compute(self, inputs) -> CalculatorResult: ...
```

#### `calculators/registry.py`

```python
@dataclass(frozen=True)
class CalculatorRegistration:
    calculator_id: str
    version: str
    tier: CalculatorTier
    instance: Calculator
    guideline_reference: Optional[str] = None

    @property
    def qualified_key(self) -> str:
        return f"calculator/{self.calculator_id}/{self.version}"   # 逐字對映規格§35版本控制格式範例

class CalculatorNotFoundError(KeyError): ...

class CalculatorRegistry:
    def register(self, calculator: Calculator, *, guideline_reference: Optional[str] = None, is_latest: bool = True) -> None: ...
    def get(self, calculator_id: str, version: Optional[str] = None) -> Calculator: ...
    def get_by_qualified_key(self, qualified_key: str) -> Calculator: ...
    def list_calculators(self, tier: Optional[CalculatorTier] = None) -> tuple[CalculatorRegistration, ...]: ...
    def compute(self, calculator_id: str, inputs, *, version: Optional[str] = None) -> CalculatorResult: ...

DEFAULT_CALCULATOR_REGISTRY = CalculatorRegistry()
```

> **open_question（採納 calculators_tier_b 的提問，未裁定）**：`get(calculator_id, version=None)` 允許隱含取 latest；規格§36 Audit Trail 要求「用哪一版公式」需完整可溯。本文件建議 `pipeline.py`/`clinical_state.py` 呼叫端一律傳入明確 `version`（由 guideline 版本設定檔決定），但未強制在型別層面禁止隱含 latest（見第4節 open_questions）。

#### Tier A（6 項，逐字實作，`clinical_status` 對照皆遵循「單次異常預設 SUSPECTED，除非呼叫端提供 corroborating 證據」原則）

| 檔案 | `calculator_id` | Inputs dataclass | 核心邏輯（規格出處） |
|---|---|---|---|
| `ckd_ga.py` | `KDIGO_GA` | `CKDGAInputs(egfr, uacr, egfr_date, uacr_date, corroborating_ckd_diagnosis: bool=False)` | KDIGO G1-G5(§6.1，G1≥90/G2 60-89/G3a 45-59/G3b 30-44/G4 15-29/G5<15)×A1-A3(A1<30/A2 30-300/A3>300)；兩值皆缺→INSUFFICIENT_DATA；任一異常→`clinical_status=SUSPECTED`（`corroborating_ckd_diagnosis=True` 時升級 `CONFIRMED`）；G1A1→COMPUTED但`clinical_status=None`。**不重用** `dm_eligibility.CKDAssessment.stage()`（P7 spec 三分類子集），只重用其 egfr/uacr 原始欄位 |
| `fib4.py` | `FIB4` | `FIB4Inputs(age_years, ast_u_l, alt_u_l, platelet_10e9_l, lab_date)` | `Age×AST/(Platelet×√ALT)`（§6.2）；任一輸入缺或 ALT/Platelet=0→INSUFFICIENT_DATA；`<1.3`→COMPUTED, `clinical_status=None`；`>=1.3`→`clinical_status=SUSPECTED`，`action_grounded_in_spec=True`（§6.2「FibroScan/VCTE、ELF」逐字文案）；年齡<35或>65附加 warning（不調整切點） |
| `bnp_hf_screen.py` | `BNP_NTPROBNP_HF_SCREEN` | `NatriureticPeptideInputs(bnp_pg_ml, nt_probnp_pg_ml, result_date, has_ckd, has_atrial_fibrillation, age_years, has_pulmonary_disease, has_anemia, has_obesity)` | `BNP>=50` 或 `NT-proBNP>=125`（§6.4）→`clinical_status=SUSPECTED`，`action_grounded_in_spec=True`（「安排Echocardiography」）；modifier 僅附加說明文字，不改變門檻 |
| `abi_tbi.py` | `ABI_TBI_PAD_SCREEN` | `ABITBIInputs(abi_right, abi_left, tbi_right, tbi_left, measurement_date, claudication_present, pedal_pulse_abnormal, ulcer_present)` | `ABI<=0.90` 異常；`ABI>1.40` noncompressible→改看 TBI（缺 TBI→該肢 INSUFFICIENT_DATA）；`TBI<=0.70` 異常（§6.5）；任一肢異常→`clinical_status=SUSPECTED` |
| `iwgdf_foot.py` | `IWGDF_FOOT_RISK` | `IWGDFFootInputs(lops_present, pad_present, foot_deformity_present, previous_foot_ulcer, previous_amputation, kidney_failure_present, last_foot_evaluation_date)` | Category 0-3（§10逐字條件，見下方對照表）；`lops_present`/`pad_present`為 None→INSUFFICIENT_DATA（不可默視為0級）；`overdue`（依 `IWGDF_FOLLOWUP_INTERVAL_DAYS` 判斷）優先蓋過 `HIGH_RISK`→`clinical_status=CARE_GAP`；否則 Category∈{2,3}→`HIGH_RISK` |
| `hypoglycemia_ada_l1.py` | `ADA_HYPO_L1` | `HypoglycemiaRiskFactorInputs(on_insulin, on_sulfonylurea, on_meglitinide, major_factors: frozenset[str], minor_factors: frozenset[str])` | 規則式（§8，非計分公式，docstring 固定附「工程規則化詮釋、需臨床覆核」警語）：無相關用藥→`clinical_status=None`(LOW)；有相關用藥+全部因子未評估→INSUFFICIENT_DATA；有相關用藥+≥1 major→`HIGH_RISK`；僅 minor→`clinical_status=None`(MODERATE，§5無「中度未來風險」層級，不臆造第5態) |

`IWGDF_FOLLOWUP_INTERVAL_DAYS: dict[int, tuple[int,int]] = {0:(365,365), 1:(180,365), 2:(90,180), 3:(30,90)}` 定義於 `iwgdf_foot.py`（唯一權威來源，`care_gap_clocks.py` import 使用）。

每個 calculator 搭配 `xxx_inputs_from_profile(profile, clinical_state_registry, as_of) -> XxxInputs` 純函式（比照 v1 `risk.build_risk_factor_snapshot()` 慣例），資料萃取與計算邏輯分離。CKD G/A adapter 直接重用 `profile.enrollment_state.ckd_assessments`；其餘 5 個 adapter 讀 `clinical_data_layer.py` 新增型別（AST/ALT/Platelet/BNP/NT-proBNP 走 `profile.lab_series_by_item`，需新增 item_code 對照常數，見第4節 open_questions）。

#### Tier B（5 項，可插拔介面，`tier_b/_base.py::TierBCalculatorBase` 共用骨架）

```python
# calculators/tier_b/_base.py
class TierBCalculatorBase:
    """子類別只需提供 calculator_id/calculator_version/required_inputs/
    model_provenance 與 _extract_inputs(profile_snapshot)->dict。compute()
    一律回傳 execution_status=REQUIRES_EXTERNAL_VALIDATED_MODEL、
    result_values=None、interpretation=None、
    action='本工具需已通過台灣本地驗證/校正之計算服務方可產生風險數值，
    目前僅顯示所需變數是否齊備；請勿以工程佔位公式推算實際風險
    （依規格書§37 Local Validation 精神）。'"""
```

| 檔案 | `calculator_id` | `required_inputs`（節錄） | 備註 |
|---|---|---|---|
| `watch_dm.py::WatchDmCalculator` | `WATCH_DM` | age_years/bmi/systolic_bp/diastolic_bp/creatinine/hdl_c/fasting_plasma_glucose/qrs_duration_ms/previous_mi/previous_cabg | 直接繼承 `TierBCalculatorBase`，無路由邏輯 |
| `prevent_ascvd.py::PreventCalculator` | `PREVENT` | systolic_bp/total_cholesterol/hdl_c/current_statin_treatment/smoking_status/egfr/bmi/diabetes_status（選填 uacr/hba1c_latest/social_deprivation_index） | 先呼叫 `already_in_secondary_prevention()`（見下）與 30–79 歲適用範圍檢查——兩者命中則 `execution_status=NOT_APPLICABLE`，`interpretation` 例外允許非None（純路由說明，非風險計算） |
| `prevent_ascvd.py::LegacyAscvdPceCalculator` | `ASCVD_PCE_2013` | 同上 + race_ethnicity, treated_hypertension | 40–79 歲範圍檢查標記 TODO（非規格逐字，是 Pooled Cohort Equations 慣例引用），`race_ethnicity` 欄位本身列為倫理待裁定項（見第4節） |
| `karter_hypoglycemia.py::KarterHypoglycemiaCalculator` | `KARTER_HYPO_ED_HOSP` | prior_hypo_related_ed_or_hosp/ed_visits_prior_12mo/insulin_use/sulfonylurea_use/ckd_stage_4_5_or_severe/age_years | `ckd_stage_4_5_or_severe` 消費 `KDIGO_GA` Tier A calculator 輸出（`stage in {"G4","G5"}`），不重複硬編 eGFR<30；`ed_visits_prior_12mo` 消費新增 `EncounterUtilizationRecord` |
| `kfre.py::Kfre4VarCalculator` | `KFRE_4VAR` | age_years, sex, egfr, uacr | `sex` 消費 `PatientClinicalProfile.sex`（見3.1節新增欄位） |

`already_in_secondary_prevention(complications: frozenset[str], has_revascularization_history: Optional[bool]) -> bool`（`prevent_ascvd.py`）：依§7逐字文字命中既有 `COMPLICATION_ICD10_PREFIXES` 的 `CVD`/`CEREBROVASCULAR`/`PVD` 類別即視為 secondary prevention（重用既有 `ComplicationReport`，鐵律7）。此函式標記為「§7逐字路由規則，非風險計算，不受 Tier B 限制」。

`tier_b/__init__.py::register_tier_b_calculators(registry: CalculatorRegistry) -> None`：對 `DEFAULT_CALCULATOR_REGISTRY` 註冊上述 5 個 calculator，`guideline_reference` 分別填 `"OpenClaw HIS §6.3"`/`"§7"`/`"§7"`/`"§9"`/`"§12"`。

---

### 3.5 Complication Detection 對照 — `complication_identification.py`（擴充）

保留 v1 既有 `COMPLICATION_ICD10_PREFIXES`/`ComplicationConfig`/`ComplicationFinding`/`ComplicationReport`/`identify_complications()` **完全不變**（作為 `clinical_state.derive_clinical_state()` 的輸入之一，不是被 Layer2 取代）。新增：

```python
# 碼表擴充（鐵律2 ICD-10-CM通用慣例，非規格書逐字條文，需臨床端確認）
COMPLICATION_ICD10_PREFIXES["HEART_FAILURE"] = ("I50",)
COMPLICATION_ICD10_PREFIXES["MASLD_MASH"] = ("K76.0", "K75.81")
COMPLICATION_ICD10_PREFIXES["OBESITY"] = ("E66",)
COMPLICATION_ICD10_PREFIXES["FOOT_ULCER_HISTORY"] = ("L97",)
COMPLICATION_ICD10_PREFIXES["AMPUTATION_HISTORY"] = ("Z89.4", "Z89.5", "Z89.6")

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
# 全管線併發症→domain 映射唯一權威來源；不改既有 COMPLICATION_ICD10_PREFIXES
# 既有 6 類 key 語意（鐵律7），只加映射層。
```

---

### 3.6 Care-Gap Agent — `care_gap_clocks.py`（新增）

包裝並重用既有 `care_gap.py`（不重算），新增規格§18 三時鐘。

```python
@dataclass(frozen=True)
class ClockEvaluation:
    item_code: str
    description: str
    clock_type: Literal["CLINICAL", "P4P", "PATIENT_SPECIFIC"]
    last_performed_date: Optional[date]
    interval_days_range: tuple[int, int]      # (min,max)；單一值時 min==max
    next_due_earliest: Optional[date]
    next_due_latest: Optional[date]
    satisfied: bool
    is_placeholder_interval: bool
    guideline: Optional[str] = None
    calculator: Optional[str] = None
    spec_reference: Optional[str] = None

def to_finding(ev: ClockEvaluation, patient_id: str, domain: ClinicalDomain) -> Optional[ClinicalFinding]:
    """僅 satisfied=False 時產生 ClinicalFinding（status=CARE_GAP）。"""

# Clinical Clock：年度預設頻率，讀既有 lab item_code（HbA1c/血脂/eGFR/UACR/眼底）
# 沿用 state.latest_lab_within()；足部/BP/體重/吸菸走 clinical_data_layer 新
# 型別，缺資料時 satisfied=False + is_placeholder_interval=True + DataGapFlag。
CLINICAL_CLOCK_REGISTRY: dict[str, "ClinicalClockRule"]
def clinical_clock_view(profile, as_of, config=None) -> list[ClockEvaluation]: ...

# P4P Clock：純包裝既有 care_gap.assess_care_gaps()，零重算
def p4p_clock_view(care_gap_report: "CareGapReport") -> list[ClockEvaluation]: ...

# Patient-Specific Clock：依併發症嚴重度動態調整頻率
class PatientSpecificClockRule(Protocol):
    def evaluate(self, profile, clinical_state: "PatientClinicalState") -> Optional[ClockEvaluation]: ...

class IWGDFFootClockRule:
    """唯一有完整數字依據的規則（§10）。消費 clinical_state 中
    IWGDF_FOOT_RISK finding 的 severity（category），對照
    calculators.iwgdf_foot.IWGDF_FOLLOWUP_INTERVAL_DAYS。不可得時優雅
    降級為 Clinical Clock 之通用年度頻率（is_placeholder_interval=True）。"""

class RetinopathySeverityClockRule:
    """★ Tier B/待補：規格§18僅質性敘述，無嚴重度分級對照頻率表，鐵律1
    禁止自行編造切點，恆降級為年度預設 + DataGapFlag。"""

class CKDMonitoringFrequencyClockRule:
    """★ 同上：§6.1「依風險增加至每年1-4次」無 G×A 對照確切次數表。"""

def advanced_screening_gap(
    watch_dm: Optional["CalculatorResult"], fib4: Optional["CalculatorResult"],
    bnp_ordered: bool, vcte_ordered: bool,
) -> list[ClinicalFinding]:
    """§19「Advanced screening: WATCH-DM high→考慮NT-proBNP／FIB-4 elevated
    →考慮VCTE」。觸發模式本身逐字對應規格§6.2/§6.3路徑敘述（trigger_
    grounded_in_spec 概念），但上游 WATCH-DM 仍是 Tier B——execution_status
    != COMPUTED 時本規則不觸發任何 high_risk 建議，只忠實呈現「尚未可用」
    的資訊性狀態，不得把「未驗證」誤判為「正常」。"""

@dataclass
class CareGapAgentReport:
    patient_id: str
    as_of_date: date
    clinical_clock: list[ClockEvaluation]
    p4p_clock: list[ClockEvaluation]
    patient_specific_clock: list[ClockEvaluation]
    advanced_screening_gaps: list[ClinicalFinding]
    warnings: list[str] = field(default_factory=list)
    data_gaps: list["DataGapFlag"] = field(default_factory=list)

def assess_care_gap_agent(
    profile, clinical_state: "PatientClinicalState", care_gap_report: "CareGapReport",
    calculator_results: Mapping[str, "CalculatorResult"] = (), config=None,
) -> CareGapAgentReport:
    """C2 直接吃既有 care_gap_report（不重跑），C1/C3 自行評估，C4 另組。"""
```

---

### 3.7 Guideline Rule Engine — `guideline_recommendation.py`（擴充）

保留 v1 既有 `EvidenceType`/`RecommendationEvidence`/`RecommendationPriority`/`GuidelineRecommendationEngine` 介面（`.build(input_data)` 簽名不變）、既有 4 條示範規則不刪。新增：

```python
@dataclass(frozen=True)
class GuidelineSource:
    guideline_id: str
    version: str
    publisher_or_authority: str
    citation: str
    last_updated: Optional[date] = None

GUIDELINE_LIBRARY: dict[str, GuidelineSource] = {
    # 規格§15逐字8項登錄；version 除 ADA_SOC_2026/IWGDF_2023/Taiwan_NHI_*_2026
    # 已內嵌年份外，KDIGO/AHA_ACC/Taiwan_DM_Guideline_2022/Taiwan_DKD_2024
    # 之 version 欄位留待人工提供正式版次（見第4節 open_questions）
    "ADA_SOC_2026": GuidelineSource("ADA_SOC_2026", "2026", "American Diabetes Association", "ADA Standards of Care in Diabetes 2026"),
    "Taiwan_DM_Guideline_2022": GuidelineSource("Taiwan_DM_Guideline_2022", "2022", "台灣糖尿病學會", ""),
    "Taiwan_DKD_2024": GuidelineSource("Taiwan_DKD_2024", "2024", "台灣腎臟醫學會", ""),
    "KDIGO": GuidelineSource("KDIGO", "", "Kidney Disease: Improving Global Outcomes", ""),
    "AHA_ACC": GuidelineSource("AHA_ACC", "", "American Heart Association / ACC", ""),
    "IWGDF_2023": GuidelineSource("IWGDF_2023", "2023", "International Working Group on the Diabetic Foot", ""),
    "Taiwan_NHI_DM_P4P_2026": GuidelineSource("Taiwan_NHI_DM_P4P_2026", "2026", "衛生福利部中央健康保險署", "P14 spec"),
    "Taiwan_NHI_CKD_P4P_2026": GuidelineSource("Taiwan_NHI_CKD_P4P_2026", "2026", "衛生福利部中央健康保險署", "P7 spec"),
}

# RecommendationRule 擴充欄位（既有 rule_id/title_template/priority/
# trigger_grounded_in_spec/action_is_placeholder_content/spec_reference/
# matcher/education_topic_code 全部保留）：
#   guideline_id: Optional[str] = None
#   recommendation_number: Optional[str] = None
#   evidence_level: Optional[str] = None        # "TODO-SPEC-VERIFY" 占位，待臨床查證ADA正式grade
#   applicable_population: Optional[str] = None
#   exclusion: Optional[str] = None
#   alert_level: Literal["information","clinical_attention","safety_alert"] = "clinical_attention"
#     # 本 Layer 規則刻意不產生 safety_alert（保留給未來 Medication Agent）
#   related_finding_id_matcher: Optional[Callable[[GuidelineRecommendationInput], Optional[str]]] = None
#     # 命中時回傳對應 ClinicalFinding.finding_id，供 compose_clinical_data_objects() join

# GuidelineRecommendation 同步擴充相同欄位（規格§30要求"結果物件"上也要有
# guideline/version，不只在規則定義上）。

# GuidelineRecommendationInput 新增（既有4欄位不動，build_guideline_input()
# 同步擴充參數，向下相容）：
#   clinical_state: Optional["PatientClinicalState"] = None
#   calculator_results: Mapping[str, "CalculatorResult"] = field(default_factory=dict)
```

新增 Tier A 規則（節錄，皆 `trigger_grounded_in_spec=True`）：`KDIGO_GA_SEVERITY_DISPLAY`、`FIB4_SECONDARY_ASSESSMENT`、`BNP_ABNORMAL_ECHO_REFERRAL`、`ABI_TBI_PAD_PATHWAY`、`IWGDF_FOOT_FREQUENCY_REMINDER`、`NHI_CKD_P4P_LAB_GAP`（直接 reuse `rules_p7.P7001_LAB_REQUIREMENTS_BASE`/`P7002_LAB_REQUIREMENTS_BASE`）。

新增 Tier B「僅資訊揭露」規則：`WATCH_DM_INFO`/`PREVENT_INFO`/`KARTER_INFO`/`KFRE_INFO`——僅在對應 `CalculatorResult.execution_status==REQUIRES_EXTERNAL_VALIDATED_MODEL` 時觸發，`alert_level="information"`，`evidence_level="risk_communication_only_pending_local_validation"`，文字禁止帶百分比門檻式建議。

---

### 3.8 Medication Intelligence Agent — `medication_intelligence.py`（新增）

```python
MEDICATION_ATC_CLASS_MAP: dict[str, tuple[str, ...]] = {
    "SGLT2_INHIBITOR": ("A10BK",), "GLP1_RA": ("A10BJ",), "METFORMIN": ("A10BA",),
    "SULFONYLUREA": ("A10BB",), "MEGLITINIDE": ("A10BX",), "DPP4_INHIBITOR": ("A10BH",),
    "TZD": ("A10BG",), "INSULIN": ("A10A",),
}   # 鐵律2：WHO ATC通用醫學編碼慣例；複方製劑ATC碼可能重疊，需藥劑部覆核

@dataclass(frozen=True)
class MedicationCheckInput:
    patient_id: str; as_of_date: date
    active_drug_classes: frozenset[str]
    clinical_state: "PatientClinicalState"     # ★ 改為直接消費 Layer2 輸出，取代原設計各自重讀 ComplicationReport/trend
    kdigo_g_stage: Optional[str]; kdigo_a_stage: Optional[str]   # 消費 KDIGO_GA CalculatorResult，不重算
    age_years: Optional[int]
    hypoglycemia_level1_result: Optional["CalculatorResult"]
    data_gaps: list["DataGapFlag"]

def build_medication_check_input(profile, clinical_state, calculator_results) -> MedicationCheckInput: ...

def assess_ada_level1_hypoglycemia_risk(inp, cfg) -> "CalculatorResult":
    """★ 註：本函式在 v2 中改為薄封裝——實際邏輯已收斂到
    calculators/hypoglycemia_ada_l1.py（唯一權威實作，避免與 Calculator
    Library 重複實作同一規則，鐵律7）。本函式僅組裝 adapter。"""

@dataclass(frozen=True)
class MedicationIndicationRule:
    rule_id: str; guideline_id: str; title_template: str
    matcher: Callable[[MedicationCheckInput], Optional["MedicationCheckEvidence"]]
    priority: "RecommendationPriority"
    trigger_grounded_in_spec: bool; action_is_placeholder_content: bool
    spec_reference: str
# 內建三條規則，逐字對應§16三個範例：KIDNEY_PROTECTIVE_THERAPY_GAP /
# SECONDARY_ASCVD_PREVENTION_GAP / HIGH_HYPOGLYCEMIA_RISK_DEINTENSIFICATION

class ContraindicationChecker(Protocol):
    def check(self, inp: MedicationCheckInput, drug_class: str) -> tuple["ContraindicationFlag", ...]: ...
class NullContraindicationChecker:
    """預設實作：全部回傳 status="not_evaluated"，避免醫師誤以為系統已
    排除禁忌症（呼應 Tier B 的 execution_status 精神）。"""

@dataclass(frozen=True)
class MedicationReviewPanel:
    indication: str; egfr_value: Optional[float]; egfr_data_gap: bool
    current_medications: tuple[str, ...]
    contraindications: tuple["ContraindicationFlag", ...]
    guideline_source: str; guideline_section_or_spec_reference: str

@dataclass(frozen=True)
class MedicationRecommendation:
    """實作 physician_decision.Reviewable Protocol。"""
    recommendation_id: str; rule_id: str; title: str; priority: "RecommendationPriority"
    related_finding_id: Optional[str]           # 對齊 guideline_recommendation.py 的 finding_id 外鍵慣例
    review_panel: MedicationReviewPanel

def build_medication_intelligence_report(
    inp: MedicationCheckInput, rules: Sequence[MedicationIndicationRule] | None = None,
) -> "MedicationIntelligenceReport": ...

def build_medication_order_draft(
    recommendation: MedicationRecommendation, decision: "PhysicianDecision", review_panel: MedicationReviewPanel,
) -> Optional["MedicationOrderDraft"]:
    """★ 只有 decision.status in (ACCEPTED, MODIFIED) 時才回傳非 None——
    用型別系統保證不存在『PENDING/DECLINED 卻能開藥』的路徑（鐵律4）。
    只到藥物 class 層級（例如"SGLT2 inhibitor"），不含特定藥品/劑量。"""
```

---

### 3.9 醫師決策 — `physician_decision.py`（擴充）

既有 `PhysicianDecisionStatus`/`PhysicianDecisionRecord`/`record_decision()`/`accepted_or_modified()`/`to_audit_trail()` **完全不變**。新增：

```python
class Reviewable(Protocol):
    """讓 GuidelineRecommendation 與 MedicationRecommendation（及未來任何
    需要走醫師決策的建議型別）都能流入同一份 PhysicianDecisionRecord。"""
    recommendation_id: str
    rule_id: str
    title: str
    priority: "RecommendationPriority"

# PhysicianDecision 新增（非破壞性）：
#   decline_category: Optional[Literal["not_applicable","contraindicated","other"]] = None
#     # status==DECLINED 時，UI 的 [Not applicable]/[Contraindicated]/[Dismiss]
#     # 三按鈕分別填入對應值；一般 GuidelineRecommendation 來源可留 None。

def present_for_decision(recommendations: Sequence[Reviewable]) -> PhysicianDecisionRecord:
    """簽名放寬（原本吃 GuidelineRecommendationReport），既有呼叫端傳
    report.recommendations 仍相容；新增 MedicationIntelligenceReport
    呼叫端可直接傳 recommendations 清單。"""
```

---

### 3.10 Alert 分級 — `alert.py`（新增）

```python
class AlertLevel(str, Enum):
    INFORMATION = "information"; CLINICAL_ATTENTION = "clinical_attention"; SAFETY_ALERT = "safety_alert"

@dataclass
class AlertClassificationConfig:
    safety_alert_categories: frozenset[str] = frozenset({
        "MEDICATION_CONTRAINDICATION", "SEVERE_HYPOGLYCEMIA_RISK",
        "DANGEROUS_LAB_RESULT", "MAJOR_DRUG_INTERACTION", "ORDER_CONFLICT",
    })   # 規格§32逐字5例，非窮舉表（is_exhaustive=False），需臨床/藥劑補齊
    is_exhaustive: bool = False
    clinical_attention_status: frozenset[ClinicalStatus] = frozenset({ClinicalStatus.HIGH_RISK, ClinicalStatus.CARE_GAP})

def classify_alert(finding: ClinicalFinding, is_safety_critical_override: bool = False, config=None) -> AlertLevel:
    """優先序：(1) override 或命中 safety_alert_categories → SAFETY_ALERT；
    (2) status in clinical_attention_status → CLINICAL_ATTENTION；
    (3) 其餘 → INFORMATION。is_safety_critical_override 是留給未來
    Medication Intelligence Agent 藥物交互作用判斷的掛勾點，v2 範圍內
    永遠不會被觸發（該邏輯尚未建置）。"""

@dataclass
class AlertReport:
    patient_id: str; as_of_date: date
    by_level: dict[AlertLevel, list[ClinicalFinding]]
    safety_alert_count: int   # 僅 >0 才彈窗，避免alert fatigue（§32原文精神）

def classify_alert_batch(findings: Sequence[ClinicalFinding], config=None) -> AlertReport: ...
```

---

### 3.11 病人衛教 — `education.py`（擴充）

既有 `EducationResource`/`EducationTopicMappingConfig`/`select_education_topics()` **保留不動**，降級為「延伸衛教資源連結」子元件。新增規格§27 結構化報告：

```python
class EducationSectionCode(str, Enum):
    GLYCEMIC = "GLYCEMIC"; RENAL = "RENAL"; CARDIAC = "CARDIAC"; HEPATIC = "HEPATIC"
    FOOT = "FOOT"; EYE = "EYE"

@dataclass(frozen=True)
class EducationSectionTemplateRule:
    section_code: EducationSectionCode
    trigger_status: tuple[ClinicalStatus, ...]
    template: str
    requires_numeric_disclosure: bool   # False=Tier B或需保護病人閱讀負擔者，模板禁止填入任何自行估算數字
    is_placeholder: bool = True
    review_status: str = "UNVERIFIED"

@dataclass
class EducationReportBuilderConfig:
    section_rules: tuple[EducationSectionTemplateRule, ...] = field(default_factory=lambda: (
        # 2 筆明確 placeholder 範例（鐵律4）：
        EducationSectionTemplateRule(EducationSectionCode.GLYCEMIC, (ClinicalStatus.HIGH_RISK, ClinicalStatus.CARE_GAP),
            "您的HbA1c最近從 {v1}% → {v2}% → {v3}%，{trend_phrase}。", requires_numeric_disclosure=True),
        EducationSectionTemplateRule(EducationSectionCode.CARDIAC, (ClinicalStatus.HIGH_RISK,),
            "目前沒有確定心臟衰竭，但因為糖尿病及其他條件，屬於較需要注意的族群，因此醫師可能會安排進一步心臟檢查。",
            requires_numeric_disclosure=False),
    ))

@dataclass(frozen=True)
class EducationReportSection:
    section_code: EducationSectionCode; title: str; body_text: str
    source_finding_ids: tuple[str, ...]
    is_placeholder: bool; review_status: str

@dataclass
class PatientEducationReport:
    patient_id: str; as_of_date: date
    sections: list[EducationReportSection]
    today_actions: list[str]        # 來源=decision_record.accepted_or_modified() 逐條 + PendingOrder
    resource_topics: list["EducationTopicSelection"]   # 重用既有 select_education_topics() 輸出
    needs_manual_review: bool
    warnings: list[str] = field(default_factory=list)

def generate_patient_education_report(
    clinical_state: "PatientClinicalState", trend_report, complication_report,
    decision_record: "PhysicianDecisionRecord", pending_orders: Sequence["PendingOrder"] = (),
    config: EducationReportBuilderConfig | None = None,
) -> PatientEducationReport:
    """依 clinical_state.findings 的 status 逐一比對 section_rules 套版；
    Tier B 來源 finding（is_placeholder=True）強制走
    requires_numeric_disclosure=False 模板，任何情況下都不得自行計算/
    編造具體風險數字。"""
```

---

### 3.12 Follow-up Agent — `followup.py`（擴充）

既有 `SUBSEQUENT_TRACKING_INTERVAL_DAYS`/`MonitoringItem`/`ComplicationMonitoringConfig`/`compute_follow_up_plan()`（P4P 到期日邏輯）**完全保留**。新增規格§28「已開立醫令完成度追蹤」（與既有邏輯是不同概念，見第2節命名總表）：

```python
PendingOrderStatus = Literal["ORDERED", "COMPLETED", "CANCELLED"]

@dataclass(frozen=True)
class PendingOrder:
    order_id: str; order_type: str; ordered_date: date; status: PendingOrderStatus
    completed_date: Optional[date] = None
    triggering_recommendation_id: Optional[str] = None   # 回溯 GuidelineRecommendation.recommendation_id
    source: str = "HIS_CPOE"

class PendingOrderSource(Protocol):
    """唯讀查詢介面，刻意不提供 predict/create 方法——本站只追蹤 HIS 既有
    醫令完成狀態，不自行產生醫令（鐵律4）。目前無預設實作（見第4節
    open_questions）。"""
    def get_pending_orders(self, patient_id: str, as_of: date) -> tuple[PendingOrder, ...]: ...

@dataclass(frozen=True)
class OrderTrackingRule:
    order_type: str; description: str; staleness_threshold_days: int
    alert_level_if_stale: "AlertLevel"; is_placeholder_threshold: bool; spec_reference: Optional[str]

@dataclass
class OrderTrackingConfig:
    rules: tuple[OrderTrackingRule, ...] = field(default_factory=lambda: (
        OrderTrackingRule("FIBROSCAN", "FibroScan檢查", 30, AlertLevel.CLINICAL_ATTENTION, True,
            "OpenClaw HIS spec §28 例句提及30天，屬敘事範例非正式規則表，需臨床確認"),
        OrderTrackingRule("ECHO", "心臟超音波", 30, AlertLevel.CLINICAL_ATTENTION, True,
            "工程沿用FibroScan同一placeholder值，規格書無獨立數字"),
    ))

@dataclass(frozen=True)
class StaleOrderItem:
    order: PendingOrder; days_overdue: int; rule: OrderTrackingRule; alert_level: "AlertLevel"; summary: str

@dataclass
class OrderTrackingReport:
    patient_id: str; as_of_date: date
    pending_orders: tuple[PendingOrder, ...]; stale_orders: list[StaleOrderItem]
    warnings: list[str] = field(default_factory=list)

def track_pending_orders(
    profile, as_of: date, order_source: PendingOrderSource | None, config: OrderTrackingConfig | None = None,
) -> OrderTrackingReport:
    """order_source 未提供時，report.warnings 記錄『未串接HIS醫令查詢介面』
    並回傳空清單，不可靜默視為『沒有逾期醫令』。"""
```

`pipeline.py` 銜接：`PipelineFinalResult` 新增 `order_tracking_report: OrderTrackingReport` 欄位，`finalize_pipeline()` 新增可選參數 `order_source: PendingOrderSource | None = None`（未提供時回傳「未串接」警告版本，不阻斷既有 education/followup 邏輯）。

---

### 3.13 Pre-Visit Diabetes Brief — `pre_visit_brief.py`（新增）

規格§21 六步驟流程對應既有 `pipeline.run_stages_1_to_7()` + 新增站點；設計為「跑完全部站點後的純格式化/組裝函式」，不重新計算任何邏輯。

```python
@dataclass(frozen=True)
class TodayMetric:
    marker_name: str; latest_value: Optional[float]; latest_date: Optional[date]
    direction: "TrendDirection"; control_tier: "QualityMetricTier"

@dataclass(frozen=True)
class ComplicationMapEntry:
    domain: ClinicalDomain; traffic_light: TrafficLight; summary_text: str
    finding_ids: tuple[str, ...]

@dataclass
class PreVisitDiabetesBrief:
    patient_id: str; as_of_date: date; generated_at: datetime
    today_widget: dict[str, TodayMetric]                       # §22 Widget1
    trend_widget: tuple["MarkerTrend", ...]                    # §22 Widget2（直接reference trend_report.marker_trends）
    complication_map: list[ComplicationMapEntry]                # §23（來源=clinical_state.domain_summaries）
    advanced_risk_widget: tuple["CalculatorResult", ...]        # §24（原樣帶出，Tier B顯示model_provenance而非數字）
    guideline_gap_widget: tuple[tuple["GuidelineRecommendation", "PhysicianDecision"], ...]  # §25
    evidence_index: dict[str, ClinicalFinding]                  # §26 Why?，key=finding_id
    alert_report: "AlertReport"                                 # §32
    data_gaps: tuple["DataGapFlag", ...]

def generate_pre_visit_brief(
    profile, trend_report, clinical_state: "PatientClinicalState",
    calculator_results: Mapping[str, "CalculatorResult"],
    guideline_report: "GuidelineRecommendationReport", decision_record: "PhysicianDecisionRecord",
) -> PreVisitDiabetesBrief:
    """純組裝函式：complication_map 直接取
    clinical_state.domain_summaries（不重新計算紅黃綠三色——這正是
    clinical_state.derive_clinical_state() 已完成的職責）；
    evidence_index 直接以 clinical_state.findings 建表；
    alert_report = alert.classify_alert_batch(clinical_state.findings)。
    """
```

建議 `pipeline.py` 在既有 `run_stages_1_to_7()` 結尾（或新增的 `run_stages_1_to_N()`）自動呼叫並把 brief 附掛在擴充後的 `PipelineRunResult.pre_visit_brief` 欄位（§21 明確要求「醫師打開病歷就看到結果」，不應是額外手動呼叫的第二步）。

---

### 3.14 Pipeline Orchestrator — `pipeline.py`（擴充）

`run_stages_1_to_7()`/`finalize_pipeline()` 既有簽名與行為**完全不變**（新參數皆有預設值，舊呼叫端零改動即可運作）。新增串接：

```python
def run_stages_1_to_7(
    state, *, eligibility_report=None, physician=None, codes_in_scope=None, eligibility_engine=None,
    profile_config=None, trend_config=None, complication_config=None,
    risk_calculator=None, risk_config=None, care_gap_config=None, include_quality_monitoring=True,
    guideline_rules=None,
    # ↓ 新增，皆有預設值 ↓
    calculator_registry: "CalculatorRegistry | None" = None,   # 預設 DEFAULT_CALCULATOR_REGISTRY
    clinical_state_config: "ClinicalStateConfig | None" = None,
    order_source: "PendingOrderSource | None" = None,
) -> "PipelineRunResult":
    """新增流程：計算 calculator_registry 中已註冊、輸入齊備的 Tier A/B
    calculator → calculator_results；derive_clinical_state(...) →
    clinical_state；build_guideline_input() 改傳入 clinical_state；
    present_for_decision() 改吃 GuidelineRecommendation ∪
    MedicationRecommendation（見3.8/3.9節 Reviewable）；最後呼叫
    generate_pre_visit_brief() 附掛在回傳物件。"""

@dataclass
class PipelineRunResult:
    profile: "PatientClinicalProfile"
    trend_report: "ClinicalTrendReport"
    complication_report: "ComplicationReport"
    risk_result: "RiskAssessmentResult"
    calculator_results: dict[str, "CalculatorResult"]      # 新增
    clinical_state: "PatientClinicalState"                  # 新增
    care_gap_report: "CareGapReport"
    care_gap_agent_report: "CareGapAgentReport"              # 新增
    guideline_report: "GuidelineRecommendationReport"
    medication_report: "MedicationIntelligenceReport"        # 新增
    decision_record: "PhysicianDecisionRecord"
    pre_visit_brief: "PreVisitDiabetesBrief"                 # 新增

@dataclass
class PipelineFinalResult:
    education_plan: "EducationPlan"                          # v1既有，保留
    education_report: "PatientEducationReport"                # 新增
    followup_plan: "FollowUpPlan"                              # v1既有，保留
    order_tracking_report: "OrderTrackingReport"               # 新增
```

---

## 4. (c) Tier A / Tier B 計算工具總表

### Tier A（規格書逐字公式/切點，`calculators/` 直接實作）

| calculator_id | 公式/切點來源 | 輸入資料狀態 |
|---|---|---|
| `KDIGO_GA` | 規格§6.1；G1-G5/A1-A3 為國際通用 KDIGO 標準分期表 | 完全由 dm_eligibility 既有 `LabResult`(eGFR/UACR) + age_years 支援，無 Layer1 缺口 |
| `FIB4` | 規格§6.2：`Age×AST/(Platelet×√ALT)`，`<1.3`/`>=1.3` | 完全由 dm_eligibility 既有 `LabResult`(AST/ALT/platelet) 支援，需新增 item_code 命名常數 |
| `BNP_NTPROBNP_HF_SCREEN` | 規格§6.4：`BNP>=50` 或 `NT-proBNP>=125` | 需新增 item_code 命名常數，資料源仍是 LabResult |
| `ABI_TBI_PAD_SCREEN` | 規格§6.5：`ABI<=0.90`/`>1.40`/`TBI<=0.70` | 新增 `VascularExam`（`clinical_data_layer.py`）支援，dm_eligibility 原無此類別 |
| `IWGDF_FOOT_RISK` | 規格§10：Category0-3 條件與追蹤頻率 | 新增 `FootNeuroExam`+`VascularExam`+既有 `CKDAssessment` 組合支援 |
| `ADA_HYPO_L1` | 規格§8：風險因子清單之規則化（非計分公式，需臨床覆核） | 新增 `HypoglycemiaEventRecord`+既有 `MedicationOrder`/`CKDAssessment`/age_years 支援 |

### Tier B（僅工具名稱+變數清單，一律 `execution_status=REQUIRES_EXTERNAL_VALIDATED_MODEL`）

| calculator_id | 規格出處 | `model_provenance.original_population`（草案文字） |
|---|---|---|
| `WATCH_DM` | §6.3：5年 incident HF risk，僅列變數與世代分組風險區間示例 | "T2DM 衍生/驗證世代（美國多中心，非台灣族群）" |
| `PREVENT` | §7：AHA PREVENT，10/30年CVD risk | "約650萬美國成人（規格§37明載）" |
| `ASCVD_PCE_2013` | §7：legacy 10年 ASCVD risk（過渡期保留項目） | Pooled Cohort Equations 原始世代；`race_ethnicity` 是否納入為倫理待裁定項（見第5節#6） |
| `KARTER_HYPO_ED_HOSP` | §9：Karter 6-variable EHR-based 工具，12個月ED/住院風險（<1%/1-5%/>5%） | Karter et al. EHR-based 驗證世代 |
| `KFRE_4VAR` | §12：4-variable KFRE，2年/5年腎衰竭風險 | 多國CKD世代（含北美/歐洲/亞洲，非專門以台灣族群建立） |

所有 Tier B `CalculatorResult.result_values` 恆為 `None`、`interpretation` 恆為 `None`（`already_in_secondary_prevention()`/年齡範圍檢查等純路由分支例外）、`model_provenance` 必填且 `warning` 固定引用 §37 原文精神（`LOCAL_VALIDATION_WARNING` 常數）。

---

## 5. (d) 六段設計彙整後的 open_questions（需人工/臨床裁定，非本文件可單方面解決）

1. **Tier B 呈現方式**：本文件裁定 Tier B 結果 → `ClinicalFinding(status=CARE_GAP, severity="pending_local_validation", is_placeholder=True)`，取代 `complication_guideline` 原提議的獨立 `PendingValidationNotice` 清單。這與規格§5 D類「缺資料」的原始定義（缺 raw data，而非缺驗證模型）不完全吻合，也直接影響§40 範例畫面（WATCH-DM 顯示為🟡High risk）能否照抄——本文件的裁定選擇「安全優先於畫面還原度」，但仍需臨床/產品端最終拍板。
2. **CKD 分期雙軌並存**：`dm_eligibility.CKDAssessment.stage()` 只回傳 P7 spec 收案用的 "1"/"2"/"3a" 三級子集，與 `calculators.ckd_ga.KDIGO_GA` 的完整 G1-G5×A1-A3 是**兩套並存、用途不同**的分期邏輯（前者收案資格判斷，後者臨床嚴重度顯示），本文件裁定兩者不可互相取代，但需要正式文件記錄以避免未來被誤合併。
3. **Confirmed CKD 是否需 chronicity 佐證**：目前 `ClinicalStateConfig.tier_a_confirmed_requires_icd_corroboration=True`（較保守：單次 eGFR/UACR 異常僅 SUSPECTED，需 ICD 診斷佐證才 CONFIRMED）。是否應改為要求「egfr/uacr 異常持續>3個月、兩次分期一致」的 KDIGO chronicity 定義，需臨床覆核。
4. **`sex` 欄位定義**：PREVENT/Legacy ASCVD PCE/KFRE 皆需要，但生理性別/病歷登記性別/社會性別在跨性別病人身上可能不同，需臨床/倫理端確認欄位定義來源，非純工程決定。
5. **Legacy ASCVD PCE 是否納入 `race_ethnicity`**：規格書本身未逐字給出此工具變數清單（只提工具名稱），本文件依公開發表慣例推測需要，但 AHA 推出 PREVENT 的動機之一正是移除 race-based 係數——是否要在 HIS 保留一個需要 race 變數的 legacy calculator，涉及倫理與資料治理決策，需臨床端與資訊倫理委員會確認。
6. **`EncounterUtilizationRecord`（ED/門診/住院場域分類）**：dm_eligibility `Encounter`（凍結）無此欄位，但 Karter 工具需要「過去12個月ED就診次數」。本文件裁定在 dm_care_pipeline 新建平行的唯讀擴充結構，不修改 dm_eligibility，但需與 dm_eligibility 維護者確認是否已有更合適的既有欄位可重用。
7. **`PatientClinicalProfile` 新增欄位的落地 owner**：`clinical_data_layer.py` 提議的一批新欄位（`vital_signs`/`ophthalmology_findings`/…）與 `sex` 屬於「第1站資料整合」擁有者範圍，需協調由誰實際落地，避免與其他組同時修改 `pipeline_models.py`/`data_integration.py` 衝突。
8. **IWGDF 病史資料來源優先權**：`FootNeuroExam.ulcer_history`（當次專科檢查記錄）與診斷 ICD-10 碼 `FOOT_ULCER_HISTORY`/`AMPUTATION_HISTORY`（歷史病歷）兩個來源，兩者不一致時的優先權/去重規則尚未定義，需 `iwgdf_foot.py` 實作者裁決。
9. **CalculatorRegistry 版本隱含 latest 語意**：規格§36 Audit Trail 要求「用哪一版公式」可完整追溯，是否應強制呼叫端一律傳入明確 `version`（不允許隱含 latest），需 Version Control/Audit Trail 策略負責人確認。
10. **KFRE calculator_id 命名**：本文件採用 `KFRE_4VAR`（比規格§35 範例 `calculator/KFRE/v1` 多了 variant 後綴，因規格書外部已知另有 6/8-variable KFRE），是否符合整體命名慣例、或應在 `CalculatorRegistration` 上另加 `variant` 欄位，需與整合架構負責人對齊。
11. **AST/ALT/Platelet/BNP/NT-proBNP 的 LIS item_code 對照表**：需與資料整合/HIS 介接團隊確認實際院內醫令代碼（比照 v1 `TrendMarkerDefinition` 對 EGFR/SBP/DBP 已知的同類缺口）。
12. **ADA Level1 低血糖用藥 ATC 前綴**（尤其 meglitinide 是否統一為 A10BX）：需藥師/藥品資料組確認。
13. **`PhysicianDecisionStatus` 與規格§25 五按鈕語意的最終對齊**：本文件裁定用 `decline_category` 三值吸收「Not applicable/Contraindicated/Dismiss」，是否符合 Layer6/UI 團隊的整體期待需要該組最終確認。
14. **`GuidelineSource.version`**：KDIGO/AHA_ACC/Taiwan_DM_Guideline_2022/Taiwan_DKD_2024 之正式版次號碼，規格§15 未逐一給出，需臨床端提供正確版次資訊來源。
15. **`evidence_level`（ADA 證據等級 A/B/E）**：目前以 `"TODO-SPEC-VERIFY"` 占位，需臨床端查證 ADA 2026 原文對應章節的正式 evidence grade。
16. **`RetinopathySeverityClockRule`/`CKDMonitoringFrequencyClockRule`**：規格§18/§6.1 只有質性敘述、無嚴重度分級對照確切追蹤頻率的數字表，需眼科/腎臟科提供正式對照表後才能升級為 Tier A。
17. **IWGDF §10 區間頻率如何轉為單一到期日**：規格給的是區間（如「6-12個月」）而非單一切點，本文件保留區間（`interval_days_range`），但排程系統多半需要單一到期日，用哪個邊界當「due」觸發點需要院內排程政策確認。
18. **PendingOrderSource 的 MVP 定位**：§28 追蹤已開立醫令需要唯讀查詢 HIS/LIS/RIS order 狀態，但這屬於§38 Phase4 Action Layer 的資料前提，§39 六大優先項目未明列它；需確認是否提前在 MVP 就實作串接，或先用 Protocol 佔位。
19. **Population Health Agent 與 Follow-up Agent 的排程職責邊界**：單一病人 order staleness 主動提醒是否該併入 Population Health Agent 每日全院掃描，或維持在 Follow-up Agent 內以單病人查詢驅動，需架構層確認（避免兩個 Agent 各自重造排程邏輯）。
20. **HbA1c/LDL 良好/不良切點本身**（沿用 v1 open_question）：`trend_analysis.QualityThresholdConfig` 數值依任務指示採用，但逐字覆核規格書未見此切點原文，需臨床/品管端以健保署正式函釋核對。
21. **併發症/準確度分類細節**（沿用 v1）：`include_broader_ihd_codes`/`include_stroke_sequelae_codes`/CKD chronicity 等既有 TODO 旗標未在 v2 改動，維持 v1 既有 open_questions（見 v1 第8節）。

---

## 6. (e) 給實作工程師的檔案清單

```
src/dm_care_pipeline/
├── __init__.py                     [不動]
├── pipeline_models.py              [修改] PatientClinicalProfile 新增 clinical_data_layer 型別欄位 + sex
├── data_integration.py             [修改] build_patient_clinical_profile() 組裝新增欄位
├── clinical_data_layer.py          [新增] Layer1擴充：VitalSignObservation/OphthalmologyFinding/
│                                    #        CardiacImagingFinding/FootNeuroExam/VascularExam/
│                                    #        ImagingStudyRef/HypoglycemiaEventRecord/ProcedureRecord/
│                                    #        EncounterUtilizationRecord/AdministrativeCareStatus/
│                                    #        ClinicalDataSourceRegistry(SourceSystemStatus)
├── clinical_data_object.py         [新增] §30/§31共用型別：ClinicalDomain/DOMAIN_DISPLAY_GROUPS/
│                                    #        ClinicalStatus/SourceSystem/EvidenceItem/ModelProvenance/
│                                    #        ClinicalFinding/compose_clinical_data_objects()
│                                    #        ★ 全管線唯一權威來源
├── clinical_state.py               [新增] Layer2：TrafficLight/DomainSummary/PatientClinicalState/
│                                    #        ClinicalStatusResolver(Protocol)/ClinicalStateConfig/
│                                    #        derive_clinical_state()
├── trend_analysis.py               [不動] v1既有，HbA1c/LDL切點唯一權威來源
├── complication_identification.py  [修改] 碼表擴充(HEART_FAILURE/MASLD_MASH/OBESITY/
│                                    #        FOOT_ULCER_HISTORY/AMPUTATION_HISTORY) +
│                                    #        COMPLICATION_CATEGORY_TO_DOMAIN；
│                                    #        identify_complications()/ComplicationFinding/
│                                    #        ComplicationReport 不變
├── risk.py                         [不動，但語意降級] RuleBasedRiskCalculator 輸出改為
│                                    #        clinical_state 中 is_placeholder=True 的示意性 finding
├── care_gap.py                     [不動] v1既有，P4P Clock 唯一權威來源
├── care_gap_clocks.py              [新增] §18 Care-Gap Agent：ClockEvaluation/CLINICAL_CLOCK_REGISTRY/
│                                    #        p4p_clock_view()/PatientSpecificClockRule/
│                                    #        IWGDFFootClockRule/RetinopathySeverityClockRule/
│                                    #        CKDMonitoringFrequencyClockRule/advanced_screening_gap()/
│                                    #        CareGapAgentReport/assess_care_gap_agent()
├── calculators/                    [新增package] Layer3 Diabetes Calculator Service
│   ├── __init__.py
│   ├── base.py                     — Calculator(Protocol)/CalculatorResult/CalculatorTier/
│   │                                  CalculatorExecutionStatus/CalculatorInputField
│   ├── registry.py                 — CalculatorRegistry/CalculatorRegistration/
│   │                                  CalculatorNotFoundError/DEFAULT_CALCULATOR_REGISTRY
│   ├── ckd_ga.py                   — CKDGACalculator/CKDGAInputs（Tier A）
│   ├── fib4.py                     — FIB4Calculator/FIB4Inputs（Tier A）
│   ├── bnp_hf_screen.py            — NatriureticPeptideHFScreenCalculator（Tier A）
│   ├── abi_tbi.py                  — ABITBICalculator（Tier A）
│   ├── iwgdf_foot.py               — IWGDFFootRiskCalculator + IWGDF_FOLLOWUP_INTERVAL_DAYS（Tier A）
│   ├── hypoglycemia_ada_l1.py      — ADAHypoglycemiaLevel1Calculator（Tier A）
│   └── tier_b/
│       ├── __init__.py             — register_tier_b_calculators()
│       ├── _base.py                — TierBCalculatorBase
│       ├── watch_dm.py             — WatchDmCalculator
│       ├── prevent_ascvd.py        — PreventCalculator/LegacyAscvdPceCalculator/
│       │                              already_in_secondary_prevention()
│       ├── karter_hypoglycemia.py  — KarterHypoglycemiaCalculator
│       └── kfre.py                 — Kfre4VarCalculator
├── medication_intelligence.py      [新增] §16-17：MedicationCheckInput/MEDICATION_ATC_CLASS_MAP/
│                                    #        MedicationIndicationRule/MedicationReviewPanel/
│                                    #        ContraindicationChecker(Protocol)/MedicationRecommendation/
│                                    #        build_medication_intelligence_report()/
│                                    #        build_medication_order_draft()
├── guideline_recommendation.py     [修改] GuidelineSource/GUIDELINE_LIBRARY 新增；RecommendationRule/
│                                    #        GuidelineRecommendation 欄位擴充；既有4條規則+Engine不變
├── physician_decision.py           [修改] 新增 Reviewable(Protocol)、decline_category 欄位；
│                                    #        既有 PENDING-only 保證不變
├── alert.py                        [新增] §32：AlertLevel/AlertClassificationConfig/classify_alert()/
│                                    #        classify_alert_batch()/AlertReport
├── education.py                    [修改] 既有 select_education_topics() 不變；新增§27：
│                                    #        EducationSectionCode/EducationSectionTemplateRule/
│                                    #        EducationReportBuilderConfig/PatientEducationReport/
│                                    #        generate_patient_education_report()
├── followup.py                     [修改] 既有 compute_follow_up_plan() 不變；新增§28：
│                                    #        PendingOrder/PendingOrderSource(Protocol)/
│                                    #        OrderTrackingConfig/OrderTrackingReport/
│                                    #        track_pending_orders()
├── pre_visit_brief.py              [新增] §21-26：PreVisitDiabetesBrief/TodayMetric/
│                                    #        ComplicationMapEntry/generate_pre_visit_brief()
└── pipeline.py                     [修改] PipelineRunResult/PipelineFinalResult 擴充新欄位；
                                     #        run_stages_1_to_7()/finalize_pipeline() 簽名向下相容

tests/dm_care_pipeline/             # 沿用 v1 建議，逐檔對應一份測試，新增：
├── test_clinical_data_layer.py
├── test_clinical_data_object.py
├── test_clinical_state.py
├── test_calculators_ckd_ga.py / test_calculators_fib4.py / ...（Tier A 6份，逐一驗證規格書切點）
├── test_calculators_tier_b.py     # 驗證 execution_status 恆為 REQUIRES_EXTERNAL_VALIDATED_MODEL、result_values 恆 None
├── test_calculator_registry.py
├── test_care_gap_clocks.py
├── test_medication_intelligence.py
├── test_alert.py
├── test_pre_visit_brief.py
└── test_pipeline_integration_v2.py  # 端到端：組一份含新欄位的 PatientEnrollmentState → 跑完全部站點 → 斷言 PreVisitDiabetesBrief 關鍵欄位
```

未來擴充點（僅留 Protocol 簽名，不在本次範圍實作，見第7節）：
- `PopulationHealthAgent`(Protocol) + `PriorityQueue`（建議獨立檔案 `population_health.py`，本次不建立）
- `ActionLayerGateway`(Protocol)（建議獨立檔案 `action_layer.py`，本次不建立；`submit_lab_order`/`submit_referral`/`submit_medication_change` 皆要求 `physician_confirmation_token`，未提供時 `raise NotImplementedError`）

---

## 7. (f) MVP 範圍邊界

比照規格書§38-39，本次架構設計**全部納入**§39「第一版真正值得優先做的六件事情」：

1. Patient Summary + Trend（`trend_analysis.py` 不動，`pre_visit_brief.py` Widget1/2）
2. Complication Map（`clinical_state.py` domain_summaries → `pre_visit_brief.py` Widget3）
3. Care Gap / P4P（`care_gap.py` 不動 + `care_gap_clocks.py` 三時鐘）
4. FIB-4 + WATCH-DM + PREVENT + KFRE（`calculators/` Tier A/B 全部涵蓋，另加 CKD G/A、ABI/TBI、IWGDF、ADA Hypo L1、Karter）
5. Medication Guideline Gap（`medication_intelligence.py` + `guideline_recommendation.py` 擴充）
6. Patient Education（`education.py` §27 擴充）

**本次架構刻意只留介面、不實作完整邏輯**（比照§38 Phase4/Phase5）：
- **Population Health Agent（§29/Phase5）**：全院掃描、Priority Queue 排序邏輯不在本次範圍；僅在第6節列出未來 `PopulationHealthAgent`(Protocol) 檔案位置建議。
- **Action Layer（§33/Phase4）**：`[Apply to HIS]` 之後的真正下醫囑（`submit_lab_order`/`submit_referral`/`submit_medication_change`）不實作；`ActionLayerGateway`(Protocol) 的方法一律要求 `physician_confirmation_token` 且未提供實作時 `raise NotImplementedError`，確保鐵律4「Human-in-the-loop」在程式層面無法被繞過。
- **`PendingOrderSource`**（§28）本次同樣只留 Protocol，無預設實作（需 HIS/LIS/RIS 介接排定後才能串接，第5節 open_question#18）。
- **Legacy ASCVD PCE 是否真的上線**（`race_ethnicity` 倫理爭議，見第5節#5）留待人工裁定，本次僅實作可插拔介面本身。

超出六件事但本次一併納入的項目（因與上述六件事緊密耦合、拆開反而增加銜接成本）：Alert 分級（`alert.py`，避免 Guideline Gap 全部彈窗）、Pre-Visit Brief 組裝層（`pre_visit_brief.py`，是六件事的共同呈現介面）、§30/§31 共用型別（`clinical_data_object.py`/`calculators/base.py`，是六件事彼此銜接的地基，若不同步建立會導致六件事各自產生形狀不同的輸出物件）。

---

*本文件由整合六段獨立設計而成，建立在 `docs/臨床決策支援管線設計.md`（v1）已落地的 `src/dm_care_pipeline/` 九站骨架之上。實作過程中若發現本文件與規格書、dm_eligibility 既有程式碼、或 v1 文件有進一步落差，應優先以 `OpenClaw for Diabetes HIS.md`、`src/dm_eligibility/` 既有程式碼與 `spec/P14_rules_spec.md`/`spec/P7_rules_spec.md` 為準，並回頭更新本文件。任何標記「★」或「TODO」的假設，正式上線前一律需要臨床/品管/藥劑/倫理端逐項覆核（見第5節 open_questions），不可視為已定案。*
