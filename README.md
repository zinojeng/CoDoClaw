# CoDoClaw — 糖尿病臨床決策支援管線

**這是 [diabetes_P4P](https://github.com/zinojeng/diabetes_P4P) 的 Part 2**：一個依 `OpenClaw for Diabetes HIS.md` 設計的糖尿病臨床決策支援管線（Clinical Decision Support pipeline）。Part 1（`dm_eligibility`，糖尿病 P14/P7 品質支付收案資格判斷引擎）在此原樣附帶，因為 Part 2 的九站流程直接以 Part 1 的 `PatientEnrollmentState`/`EligibilityReport` 為輸入，兩者無法拆開運作。

**這是一個決策支援系統，不是自動診療系統**：管線只產出「有明確依據」的建議，醫師決策站刻意不提供任何「自動核准/自動下醫囑」的路徑——任何建議在醫師明確記錄決定之前，永遠停留在待決（PENDING）狀態；任何未經驗證的風險計算工具（Tier B）永遠不會產出偽裝成已驗證的數值。

## 目錄結構

```
CoDoClaw/
├── README.md                        本文件
├── requirements.txt                  最小相依套件（pytest）
├── OpenClaw for Diabetes HIS.md      Part 2 設計所依據的原始規格文件
├── spec/
│   ├── P14_rules_spec.md              P14 收案規則規格書（Part 1 依據，附帶供追溯）
│   └── P7_rules_spec.md               P7 收案規則規格書（Part 1 依據，附帶供追溯）
├── docs/
│   ├── 系統設計說明.md                  Part 1 架構說明
│   ├── 臨床決策支援管線設計.md            Part 2 v1 九站骨架設計文件
│   └── 臨床決策支援管線設計_v2_OpenClaw.md  Part 2 v2 擴充架構文件（六段設計整合裁定）
├── src/
│   ├── dm_eligibility/                Part 1：收案資格判斷引擎（依賴，非本репo重點）
│   └── dm_care_pipeline/              Part 2：臨床決策支援管線（本repo主體）
└── tests/
    ├── conftest.py
    ├── test_engine.py                 Part 1 測試
    ├── test_care_pipeline.py          Part 2 v1 測試
    └── dm_care_pipeline/              Part 2 v2 測試（21個檔案）
```

## 快速開始

```bash
pip install -r requirements.txt
pytest tests/ -q   # 313 個測試
```

## 架構：Layer 1-7 + Calculator Service

Part 2 從資料整合開始，依序完成臨床趨勢分析、併發症辨識、風險計算、Care Gap 分析，產生「有明確依據」的臨床建議（含藥物治療缺口建議），交給醫師逐筆採納/修改/婉拒，再依醫師的決定產出病人衛教內容與後續追蹤排程——最後把回診日期回饋成下一輪 `PatientEnrollmentState`，重新餵回 Part 1 的 `EligibilityEngine.evaluate()`，形成一個可持續運作的封閉迴圈。

```
Part 1（凍結，只 reuse 不改）
  PatientEnrollmentState ──▶ EligibilityEngine.evaluate() ──▶ EligibilityReport
                                        │
                                        ▼
┌──────────────────────────────────────────────────────────────────────┐
│ Layer 1 — Clinical Data Layer                                          │
│  data_integration.py / clinical_data_layer.py                          │
│  → PatientClinicalProfile  ★ 全管線唯一共同輸入                          │
└───────────────────────────────────┬────────────────────────────────────┘
        ┌───────────────────────────┼──────────────────────────────┐
        ▼                           ▼                              ▼
 trend_analysis.py    complication_identification.py      care_gap.py
        │                           │                              │
        │              ┌────────────┴───────────┐                  │
        │              ▼                         ▼                  │
        │        risk.py（illustrative）   calculators/（Tier A/B）  │
        │              │                         │                  │
        └──────────────┴────────────┬────────────┘──────────────────┘
                                     ▼
                    Layer 2 — clinical_state.py :: derive_clinical_state()
                    → PatientClinicalState（findings / domain_summaries / TrafficLight）
                    ★ 全管線唯一「病人臨床事實來源」
                                     │
        ┌────────────────────────────┼─────────────────────────────┐
        ▼                            ▼                              ▼
guideline_recommendation.py  medication_intelligence.py    care_gap_clocks.py
        └────────────────────────────┴──────────────┬───────────────┘
                                                      ▼
                          physician_decision.py :: present_for_decision()
                          → PhysicianDecisionRecord（全 PENDING）
                          ★ 醫師逐筆 record_decision()，無任何自動核准路徑
                                                      │
                        ┌─────────────┬───────────────┼──────────────┐
                        ▼             ▼               ▼              ▼
                   alert.py    education.py     followup.py   pre_visit_brief.py
                                                                     │
                                                                     ▼
                                              下一輪 PatientEnrollmentState
                                              → 回到 Part 1（封閉迴圈）
```

### 模組對照

```
src/dm_care_pipeline/
├── pipeline_models.py              PatientClinicalProfile / DataGapFlag（v2 擴充 sex/vitals/exams 等欄位）
├── data_integration.py             build_patient_clinical_profile()
├── clinical_data_layer.py          Layer1 擴充型別：VitalSignObservation/OphthalmologyFinding/
│                                    FootNeuroExam/VascularExam/CardiacImagingFinding/
│                                    ClinicalDataSourceRegistry（GRAY 燈號機制）
├── clinical_data_object.py         §30/§31 共用型別：ClinicalDomain/ClinicalStatus/ClinicalFinding/
│                                    ModelProvenance/compose_clinical_data_objects()
├── clinical_state.py               Layer2：derive_clinical_state() → PatientClinicalState
├── trend_analysis.py               QualityMetricTier/analyze_clinical_trends()
├── complication_identification.py  COMPLICATION_ICD10_PREFIXES/identify_complications()
├── risk.py                         RuleBasedRiskCalculator（illustrative placeholder）/assess_risk()
├── care_gap.py                     CARE_GAP_REGISTRY/assess_care_gaps()
├── care_gap_clocks.py              三時鐘：Clinical/P4P/Patient-Specific + Advanced Screening Gap
├── calculators/                    Layer3 Diabetes Calculator Service
│   ├── base.py / registry.py         CalculatorResult/CalculatorRegistry（唯一權威定義）
│   ├── ckd_ga.py, fib4.py, ...        Tier A（6項，規格書逐字公式/切點）
│   └── tier_b/                        Tier B（5項，WATCH-DM/PREVENT/KFRE/Karter/Legacy ASCVD PCE，
│                                        永遠回傳 REQUIRES_EXTERNAL_VALIDATED_MODEL，不冒充已驗證數值）
├── guideline_recommendation.py     GuidelineRecommendationEngine（14條規則）/GUIDELINE_LIBRARY
├── medication_intelligence.py      Guideline-Directed Medication Check（3條內建規則）
├── physician_decision.py           PhysicianDecisionRecord/present_for_decision()/Reviewable
├── alert.py                        AlertLevel 三級分級（information/clinical_attention/safety_alert）
├── education.py                    EducationTopicMappingConfig + §27結構化衛教報告
├── followup.py                     compute_follow_up_plan() + §28醫令完成度追蹤
├── pre_visit_brief.py              generate_pre_visit_brief()（Widget 1-6 純組裝層）
└── pipeline.py                     薄編排層：run_stages_1_to_7() / finalize_pipeline()
```

完整的整合裁定過程、規格書明文依據 vs 工程假設對照表、六段獨立設計如何統一命名/型別、以及仍待人工協調事項清單，請見 `docs/臨床決策支援管線設計_v2_OpenClaw.md`。

## ⚠️ 使用前必讀：placeholder 與需臨床覆核事項

Part 2 大量使用「規格書沒有明文、但決策支援管線需要」的數值與邏輯，皆已用具名 `Config` 欄位 + 程式碼註解明確標示 `★`，**不可直接視為已驗證的臨床規則上線使用**。重點包括（完整清單見 `docs/臨床決策支援管線設計_v2_OpenClaw.md` 第5節 open_questions）：

1. **HbA1c/LDL 良好/不良切點**、**風險計算公式**（`risk.RuleBasedRiskCalculator`，illustrative placeholder，非已驗證公式）——需臨床/品管端核對正式函釋。
2. **Tier B 計算工具**（WATCH-DM/PREVENT/KFRE/Karter/Legacy ASCVD PCE）——規格書只給出工具名稱與所需變數，無完整回歸係數，程式刻意設計為「永遠不會自行編造係數算出數值」，只顯示「本地驗證前不可作為風險分級依據」，正式上線前需完成台灣本地驗證/校正。
3. **ADA Level 1 低血糖風險分級**、**KDIGO CKD G/A 分期判定**、**IWGDF 糖尿病足風險分級**等 Tier A 計算工具的規則化詮釋（例如單次異常是否需 ICD 診斷佐證才算 CONFIRMED）——皆為工程規則化詮釋，需臨床覆核判斷邏輯排列組合是否恰當。
4. **藥物 ATC 分類對照表**（`medication_intelligence.MEDICATION_ATC_CLASS_MAP`）——複方製劑 ATC 碼可能重疊，需藥劑部覆核。
5. **ICD-10-CM 併發症碼表**（`complication_identification.COMPLICATION_ICD10_PREFIXES`）——部分前綴（如足潰瘍/肥胖相關）涵蓋範圍較廣，需臨床端確認精確度。
6. **病人衛教文案**——`EducationTopicMappingConfig`/§27 模板僅放明確標示 `is_placeholder=True` 的範例，不是正式衛教教材。
7. **後續追蹤監測間隔**——足部檢查/CKD複評間隔、醫令逾期閾值等皆為工程佔位值，需依實際採用之照護指引（ADA/台灣糖尿病學會）訂定。

## 授權 / License

本 repo 目前未附加授權條款；如需公開使用/散布，請先與作者確認授權方式。
