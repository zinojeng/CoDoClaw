# OpenClaw for Diabetes HIS
## Diabetes Clinical Agent × Hospital Information System
### 糖尿病智慧臨床協作代理人完整架構與需求規格草案 V1.0

---

# 1. 專案定位

本專案希望在醫院既有 HIS / EMR / LIS / PACS / CVIS / CPOE 與糖尿病照護系統之上，建立一個類似 **OpenClaw Agent Framework** 的智慧型糖尿病臨床協作代理人：

# OpenClaw for Diabetes HIS

它不是單純的 Chatbot，也不是單純 Dashboard。

核心目標是讓 AI Agent 能夠在病人進入門診前，自動完成：

**Data → Timeline → Disease Detection → Risk Calculation → Care Gap → Guideline Matching → Recommendation → Physician Action → Patient Education → Follow-up**

醫師打開病歷時，不需要再主動問：

> 「這個病人最近怎麼樣？」

而是系統已經先完成一次「糖尿病病歷預讀」。

---

# 2. 最終希望回答醫師的七個問題

每一次糖尿病門診，OpenClaw Diabetes Agent 應主動回答：

1. **這位病人的糖尿病目前控制如何？**
2. **過去 1–3 年趨勢是改善還是惡化？**
3. **目前有哪些已確認的 microvascular / macrovascular complications？**
4. **有哪些疾病尚未確診，但目前屬於高風險？**
5. **有哪些應做但還沒做的檢查？**
6. **目前治療是否符合最新 guideline？**
7. **今天最值得做的 1–5 個 action 是什麼？**

因此 OpenClaw 的價值不是「回答很多問題」，而是：

# Prioritize what matters today.

---

# 3. 整體系統架構

建議拆成七層。

## Layer 1 — Clinical Data Layer

從既有醫院系統取得：

### HIS / EMR
- Age
- Sex
- Diabetes type
- Diabetes duration
- ICD-10 diagnosis
- past medical history
- admission history
- outpatient history
- smoking
- blood pressure
- height
- weight
- BMI

### LIS
- HbA1c
- fasting glucose
- random glucose
- creatinine
- eGFR
- UACR
- lipid profile
- AST
- ALT
- platelet
- potassium
- hemoglobin
- NT-proBNP / BNP
- other biomarkers

### Medication / CPOE
- glucose-lowering medications
- insulin
- ACEi / ARB
- SGLT2 inhibitor
- GLP-1 RA
- finerenone / nsMRA
- statin
- ezetimibe
- PCSK9 inhibitor
- antihypertensive agents

### CVIS
- ECG
- QRS duration
- echocardiography
- LVEF
- diastolic dysfunction
- structural heart disease
- previous PCI / CABG

### PACS
- liver ultrasound
- CT/MRI
- vascular imaging
- cardiac imaging

### Ophthalmology
- Fundus
- VeriSee AI
- DR classification
- DME
- ophthalmology diagnosis

### Foot / Neurology
- 10-g monofilament
- vibration
- temperature/pinprick
- NCV
- ulcer
- Charcot foot
- amputation

### Vascular
- ABI
- TBI
- Doppler
- angiography
- revascularization

### Hospital administrative system
- P4P enrollment
- diabetes shared-care status
- CKD P4P
- appointment
- laboratory scheduling
- referral
- diabetes educator
- dietitian

目前原始 CoDoClaw 架構本身已經規劃 HIS、PACS、CVIS 及內分泌、心臟、腎臟、眼科等跨系統資料整合，因此這一層可視為既有架構的延伸。

---

# 4. Layer 2 — Patient Clinical State

所有資料進來後，不直接交給 LLM。

首先建立一個：

# Patient Clinical State

例如：

Diabetes:
- T2DM
- duration 12 years
- HbA1c 8.2%

Kidney:
- CKD G3aA2
- eGFR declining
- UACR increasing

Eye:
- No DR documented
- last screening 14 months ago

Heart:
- No known HF
- WATCH-DM high risk
- NT-proBNP not available

Liver:
- FIB-4 1.62
- needs secondary fibrosis assessment

Foot:
- LOPS present
- IWGDF risk 1

ASCVD:
- Previous ischemic stroke

這個 Clinical State 是後面所有 Agent 共用的「病人事實來源」。

---

# 5. 臨床狀態一定要區分四種層級

這是整個系統最重要的 safety design。

不能看到 calculator abnormal 就直接顯示：

> Disease (+)

而應區分：

## A. Confirmed disease
已有明確臨床證據。

例如：
- previous MI
- ischemic stroke
- confirmed DR
- established HF
- confirmed CKD

## B. Suspected / Possible disease
檢驗或檢查異常，但仍需確認。

例如：
- first UACR 120 mg/g
- abnormal retinal AI
- abnormal monofilament

## C. High-risk state
沒有疾病證據，但是 calculator 顯示未來事件風險高。

例如：
- WATCH-DM high risk
- PREVENT high cardiovascular risk
- KFRE high kidney failure risk

## D. Care gap / insufficient data
不是疾病，也不是高風險，而是「缺資料」。

例如：

> Retinal screening overdue.

這四種狀態在 UI 上必須明確不同。

---

# 6. Layer 3 — Diabetes Calculator Library

這應該獨立成：

# Diabetes Calculator Service

不要把公式直接寫進 LLM Prompt。

---

# 第一批 Calculator

## 6.1 CKD G/A Classification

Input：
- eGFR
- UACR

Output：

### G stage
G1  
G2  
G3a  
G3b  
G4  
G5

### Albuminuria
A1  
A2  
A3

最後顯示：

> CKD G3aA2

ADA 2026 建議 T2DM 至少每年評估 eGFR 與 UACR；已有 CKD 者則依風險增加至每年 1–4 次監測。

---

# 6.2 FIB-4

目的：

不是診斷 fatty liver。

而是：

# Advanced liver fibrosis risk assessment

公式：

FIB-4 =  
Age × AST  
──────────────  
Platelet × √ALT

需要：

- Age
- AST
- ALT
- platelet

一般 clinical pathway：

### FIB-4 <1.3
較低 advanced fibrosis risk

### FIB-4 ≥1.3
考慮第二階段評估：

- FibroScan / VCTE
- ELF
- hepatology pathway

高齡及年輕患者需有 age-related interpretation warning，而不是所有年齡機械性使用同一 cutoff。

系統顯示應為：

> FIB-4 = 1.62  
> Advanced fibrosis risk requires further assessment

不能顯示：

> Fatty liver confirmed.

---

# 6.3 WATCH-DM

目的：

# 預測 T2DM 未來約 5 年 incident HF risk

不是 HF diagnosis。

主要變數：

- BMI
- Age
- SBP
- DBP
- Creatinine
- HDL-C
- Fasting plasma glucose
- QRS duration
- Previous MI
- Previous CABG

原始 WATCH-DM 研究中，最低 risk group 的 5-year HF incidence 約 1.1%，最高組約 17.4%。

因此可以建立：

WATCH-DM  
↓  
High HF risk  
↓  
考慮 natriuretic peptide screening

但不能：

WATCH-DM high  
↓  
直接診斷 HF。

---

# 6.4 BNP / NT-proBNP HF Screening

ADA 2026 已提出糖尿病成人可考慮以 natriuretic peptide 進行 asymptomatic HF screening。

可使用：

BNP ≥50 pg/mL

或

NT-proBNP ≥125 pg/mL

作為 abnormal screening biomarker。

若異常：

→ Echocardiography  
→ 評估 stage B HF / structural heart disease

但 NT-proBNP abnormal 本身不是 HF diagnosis。

而且需要加入：

- CKD
- AF
- age
- pulmonary disease
- anemia
- obesity

等 interpretation modifier。

---

# 6.5 ABI / TBI

目的：

PAD screening / evaluation。

可依 guideline 定義：

ABI ≤0.90：
abnormal

ABI >1.40：
noncompressible

若糖尿病/CKD 導致 ABI unreliable：

→ TBI

TBI ≤0.70：
abnormal

並整合：

- claudication
- pedal pulse
- ulcer
- vascular imaging
- revascularization

---

# 第二批 Calculator Library

# 7. PREVENT / ASCVD Risk Engine

這一個非常適合 OpenClaw。

傳統系統通常只有：

> ASCVD：Yes / No

未來應該分：

### Secondary prevention

如果已有：
- MI
- ACS
- stroke
- PAD
- revascularization

則直接進入 established ASCVD pathway。

不需要再用 primary prevention calculator 決定是不是高風險。

---

## Primary prevention

對尚未有 clinical CVD 的患者，可加入：

# AHA PREVENT

PREVENT 適用於：

**30–79 歲且沒有已知 cardiovascular disease 的成人**

可估計：

- 10-year CVD risk
- 30-year CVD risk

並將：

- cardiovascular health
- kidney health
- metabolic health

一起納入。

UACR、HbA1c、social deprivation index 等亦可作為額外 predictors。

PREVENT 相較傳統 risk calculator 的一個優點，是除了 ASCVD，也把 HF 納入 total CVD risk 的框架。

因此 HIS 可以顯示：

> PREVENT 10-year CVD risk = XX%

> PREVENT 30-year CVD risk = XX%

再搭配：

- LDL
- BP
- smoking
- CKD
- obesity
- diabetes
- current statin treatment

形成 preventive recommendation。

---

## ASCVD / PREVENT Transition Layer

第一版建議不要把 legacy ASCVD calculator 完全刪掉。

可以同時保留：

**Legacy 10-year ASCVD**

以及：

**PREVENT**

原因是不同 guideline / research / clinical workflow 尚可能使用不同工具。

但長期 architecture 應以：

# Calculator versioning

管理，例如：

PREVENT_2023_v1  
ASCVD_PCE_2013_v1

而不是只叫：

“CV risk score”。

---

# 8. Hypoglycemia Risk Engine

這部分非常適合糖尿病 HIS，因為很多資料本來就在 EHR。

建議分兩層。

---

## Level 1 — ADA Clinical Hypoglycemia Risk

首先檢查病人是否使用：

- insulin
- sulfonylurea
- meglitinide

然後找 high-risk factors。

ADA 2026 的主要 high-risk factors 包括：

- 近 3–6 個月 Level 2/3 hypoglycemia
- intensive insulin treatment
- impaired hypoglycemia awareness
- kidney failure
- cognitive impairment / dementia
- history of metabolic surgery

另外還有：

- recurrent Level 1 hypoglycemia
- basal insulin
- age ≥75
- high glucose variability
- polypharmacy
- cardiovascular disease
- CKD

等其他 risk factors。

因此可以建立：

> Hypoglycemia risk: HIGH

Evidence:
- insulin
- CKD G4
- age 81
- previous severe hypoglycemia

Action：
- review insulin dose
- consider CGM
- review sulfonylurea
- adjust glycemic target
- hypoglycemia education

---

# 9. Karter Hypoglycemia Risk Stratification Tool

第二層可以增加已驗證的 EHR-based model。

Karter tool 只需要 6 個 EHR variables：

1. Previous hypoglycemia-related ED/hospital utilization
2. ED visits in previous 12 months
3. Insulin use
4. Sulfonylurea use
5. CKD stage 4–5 / severe kidney disease
6. Age

預測：

# 未來 12 個月 hypoglycemia-related ED / hospitalization

分成：

Low：
<1%

Intermediate：
1–5%

High：
>5%

此工具本身就是以 EHR implementation 為目的開發，因此非常符合 OpenClaw population-management architecture。

但台灣上線前仍應做 local validation。

---

# 10. Diabetic Foot Risk Engine

不要只有：

> Monofilament normal / abnormal.

建議導入：

# IWGDF Risk Classification

---

## Category 0 — Very low

No LOPS  
and  
No PAD

追蹤：

每年一次。

---

## Category 1 — Low risk

LOPS

或

PAD

追蹤：

每 6–12 個月。

---

## Category 2 — Moderate risk

例如：

LOPS + PAD

或

LOPS + foot deformity

或

PAD + foot deformity

追蹤：

每 3–6 個月。

---

## Category 3 — High risk

LOPS 或 PAD

加上：

- previous foot ulcer
- previous amputation
- kidney failure

追蹤：

每 1–3 個月。

ADA 2026 已直接採用 IWGDF risk stratification 與相對應檢查頻率。

因此 OpenClaw 可以直接產生：

> IWGDF Foot Risk = 3  
> Previous ulcer + LOPS  
> Last foot evaluation = 5 months ago  
> Status = overdue

這比只記：

> foot exam done

臨床價值高很多。

---

# 11. CKD Progression Engine

這一個建議做成三個 component。

---

## Component A — KDIGO G/A Risk

eGFR + UACR

產生：

G stage  
A stage

以及相對應：

- monitoring frequency
- progression risk
- referral priority

---

## Component B — Longitudinal eGFR / UACR

系統自動分析：

### eGFR slope

例如：

71  
→ 65  
→ 59  
→ 52

不要只顯示：

> eGFR 52

而要顯示：

> Progressive decline detected.

另外追蹤：

### UACR trajectory

42  
→ 89  
→ 176  
→ 332 mg/g

可以比單次 lab 更早辨識 deterioration。

---

# 12. KFRE — Kidney Failure Risk Equation

對 CKD 患者加入：

# Kidney Failure Risk Equation

最適合 HIS 的是：

## 4-variable KFRE

需要：

- Age
- Sex
- eGFR
- UACR

即可預測：

### 2-year kidney failure risk

以及

### 5-year kidney failure risk

KFRE 已在大型 multinational CKD cohort 進行驗證，而 4-variable version 特別容易整合進 EMR/LIS。

例如：

> CKD G4A3  
> KFRE 2-year kidney failure risk = 18%  
> KFRE 5-year kidney failure risk = 46%

↓

Agent 可提示：

> High kidney failure risk.

並觸發：

- nephrology referral review
- renal replacement planning consideration
- closer laboratory monitoring
- medication optimization

---

# 13. Calculator Library 最終架構

第一階段可以形成以下核心：

### Kidney
- eGFR
- CKD G/A
- eGFR slope
- UACR trajectory
- KFRE

### Liver
- FIB-4

### Heart Failure
- WATCH-DM
- BNP
- NT-proBNP

### ASCVD / CKM
- PREVENT
- legacy ASCVD risk

### Hypoglycemia
- ADA hypoglycemia risk
- Karter Hypoglycemia Risk

### Foot
- IWGDF foot risk

### PAD
- ABI
- TBI

未來增加 calculator 時：

只增加一個：

# Calculator Module

而不是重新做一個 Bot。

---

# 14. Layer 4 — Complication Detection Agent

所有 calculator 與 clinical data 最後交給：

# Diabetes Complication Agent

建立完整：

### Microvascular

Kidney  
Retinopathy  
Neuropathy  
Diabetic foot

### Macrovascular

Coronary ASCVD  
Cerebrovascular disease  
PAD

### Cardiometabolic complications

Heart failure  
CKD  
MASLD / MASH  
Obesity

每項都顯示：

Status  
Evidence  
Date  
Source  
Risk  
Next action

---

# 15. Layer 5 — Guideline Rule Engine

Clinical Agent 不應直接依 LLM knowledge 決定治療。

應建立：

# Version-Controlled Guideline Library

例如：

ADA_SOC_2026

Taiwan_DM_Guideline_2022

Taiwan_DKD_2024

KDIGO

AHA_ACC

IWGDF_2023

Taiwan_NHI_DM_P4P_2026

Taiwan_NHI_CKD_P4P_2026

每一條 recommendation 都要知道：

- guideline
- version
- recommendation number
- evidence level
- applicable population
- exclusion
- last updated date

ADA 2026 本身已明確要求糖尿病評估包含 ASCVD/HF history、10-year ASCVD risk、CKD staging、hypoglycemia risk、retinopathy、neuropathy 以及 MASLD/MASH，因此這套架構其實與現行 comprehensive diabetes evaluation 非常吻合。

---

# 16. Medication Intelligence Agent

Clinical State 完成後進入：

# Guideline-Directed Medication Check

例如：

T2DM  
HbA1c 6.9%  
CKD G3aA3  
No SGLT2 inhibitor

不能只回答：

> HbA1c good.

Agent 應提示：

> Kidney-protective therapy gap detected.

又例如：

Previous stroke  
LDL 92 mg/dL

↓

> Secondary ASCVD prevention pathway.

又例如：

Age 82  
CKD G4  
recurrent hypoglycemia  
SU + insulin

↓

> High hypoglycemia risk.  
> Consider treatment deintensification / medication review.

ADA 2026 仍強調 T2DM 合併 established ASCVD 或 CKD 時，具 cardiovascular/kidney benefit 的 SGLT2 inhibitor 或 GLP-1 RA 應納入 comprehensive risk-reduction plan。

---

# 17. Medication Agent 不應直接開藥

設計原則：

# Read → Detect → Recommend → Physician Approve → Execute

例如：

Agent：

> Consider SGLT2 inhibitor.

醫師點：

[Review]

才看到：

- indication
- eGFR
- current medications
- contraindications
- guideline source

最後才有：

[Open HIS medication order]

醫師確認後才真正送出。

---

# 18. Care-Gap Agent

另外建立：

# Diabetes Care-Gap Engine

同時有三個 clock。

---

## Clinical Clock

依 clinical guideline：

> 什麼時候應該做？

---

## P4P Clock

依台灣制度：

> 這一期還缺什麼？

---

## Patient-Specific Clock

依 complication severity：

> 這個人是不是需要比一般頻率更密集？

例如：

Retinal exam：

一般可能 yearly。

但已有 significant DR：

可能需要更密集追蹤。

Diabetic foot：

IWGDF 0：
yearly

IWGDF 3：
1–3 months

所以不能單純所有糖尿病病人都寫：

> annual foot exam.

---

# 19. P4P Agent

Bot 主動檢查：

- HbA1c
- lipid
- creatinine/eGFR
- UACR
- retinal exam
- foot exam
- BP
- body weight
- smoking
- education
- relevant P4P requirements

產生：

# This Visit Care Gap

例如：

Completed:
✓ HbA1c  
✓ LDL  
✓ eGFR  
✓ UACR

Missing:
⚠ Foot examination

Due soon:
⚠ Retinal exam due in 2 months

Advanced screening:
⚠ WATCH-DM high → consider NT-proBNP
⚠ FIB-4 elevated → consider VCTE

---

# 20. OpenClaw Agent Team

前台看起來可以只有一隻：

# Diabetes Copilot

但後台建議拆成多個 agent。

### Diabetes Orchestrator Agent
負責協調其他 Agent。

### Timeline Agent
負責 longitudinal history。

### Complication Agent
判斷 complication status。

### Calculator Agent
呼叫 deterministic calculator service。

### Guideline Agent
取得對應 guideline rule。

### Medication Agent
尋找 treatment gap / conflict。

### P4P Agent
檢查 program care gap。

### Patient Education Agent
產生個人化衛教。

### Follow-up Agent
追蹤尚未完成 action。

### Population Health Agent
處理全院糖尿病族群。

---

# 21. 門診前工作流程

在病人尚未進診間前：

OpenClaw 自動：

讀取病歷

↓

更新 Patient Clinical State

↓

重新計算 calculators

↓

搜尋 complication evidence

↓

檢查 guideline

↓

檢查 P4P

↓

產生：

# Pre-Visit Diabetes Brief

因此醫師打開病歷就看到結果。

---

# 22. 醫師門診 Widget

主畫面建議不要是一堆文字。

---

## Widget 1 — Today

HbA1c  
BP  
Weight  
LDL  
eGFR  
UACR

旁邊：

↑ ↓ →

表示 trend。

---

## Widget 2 — 3-Year Trend

HbA1c graph

Weight graph

eGFR graph

UACR graph

LDL graph

讓醫師直接拿來進行醫病溝通。

---

# 23. Complication Map Widget

例如：

🔴 Kidney  
CKD G3aA2

🟢 Eye  
No DR

🟡 Neuropathy  
Screening overdue

🔴 ASCVD  
Previous stroke

🟡 HF  
WATCH-DM high risk

🟡 Liver  
FIB-4 1.6

🟢 PAD  
No evidence

---

# 24. Advanced Risk Widget

集中顯示：

PREVENT

WATCH-DM

KFRE

FIB-4

Hypoglycemia Risk

IWGDF Foot Risk

例如：

> PREVENT 10-year CVD risk: ↑

> WATCH-DM: High

> KFRE 5-year: 24%

> FIB-4: 1.62

> Hypoglycemia: High

> IWGDF: 2

---

# 25. Guideline Gap Widget

顯示：

### Medication

⚠ CKD + albuminuria without SGLT2i

✓ ACEi/ARB

⚠ LDL above patient-specific goal

### Screening

⚠ Retinal exam overdue

⚠ Foot exam overdue

### Advanced risk

⚠ WATCH-DM high

⚠ FIB-4 intermediate/high

醫師可以直接：

[Review]

[Accept]

[Not applicable]

[Contraindicated]

[Dismiss]

---

# 26. Evidence Widget

每一個 AI 判斷都必須可以點開：

# Why?

例如：

CKD G3aA2

Evidence：

eGFR 52 — 2026/08

eGFR 55 — 2026/05

UACR 176 — 2026/08

UACR 142 — 2026/05

Source：

LIS

Guideline：

ADA 2026 / CKD rule

醫師必須能知道：

# Agent 為什麼這樣判斷？

---

# 27. 個人化 Patient Education

門診完成後：

Agent 利用：

- HbA1c trend
- complications
- calculators
- medication changes
- today plan

自動產生：

# 我的糖尿病健康報告

例如：

## 血糖

您的 HbA1c 最近從：

7.1 → 7.6 → 8.2%

目前呈現上升趨勢。

---

## 腎臟

目前尿蛋白較高，腎功能也需要持續追蹤。

---

## 心臟

目前沒有確定心臟衰竭，但因為糖尿病及其他條件，屬於較需要注意的族群，因此醫師可能會安排進一步心臟檢查。

---

## 肝臟

目前抽血計算結果顯示需要進一步評估肝纖維化風險。

---

## 今天我們做的事情

- 調整藥物
- 安排眼底
- 安排 NT-proBNP
- 安排 FibroScan
- 三個月後抽血

---

# 28. Follow-up Agent

Agent 不應在病人離開門診後就停止。

例如：

FibroScan ordered

但 30 天後仍未完成。

↓

Agent 標記：

> Ordered but not completed.

或者：

NT-proBNP abnormal

↓

Echo ordered

↓

Echo 尚未完成

↓

下一次門診：

> Pending echocardiography.

---

# 29. Population Health Agent

進一步可以每天掃描全院糖尿病患者。

例如：

8,000 位糖尿病患者

↓

找出：

- CKD + albuminuria 尚無 appropriate therapy
- HbA1c >9%
- rapidly deteriorating renal function
- retinal screening overdue
- IWGDF high-risk foot
- high hypoglycemia risk
- high WATCH-DM
- high PREVENT
- high KFRE
- elevated FIB-4 without subsequent assessment

最後不是丟出 2,000 個 alert。

而是：

# Priority Queue

例如：

今天最值得介入的 30 位病人。

這與原 CoDoClaw 已提出的分級、分群、動態風險管理與 precision public health 概念可以直接連接。

---

# 30. OpenClaw Clinical Data Object

每個臨床結果建議都存成：

### condition
CKD

### status
confirmed

### severity
G3aA2

### evidence
eGFR 52  
UACR 176

### source
LIS

### date
2026-08-20

### calculator
KDIGO_GA

### calculator_version
v1.0

### guideline
ADA_SOC_2026

### recommendation
Consider kidney-protective therapy review

### action_status
pending

### clinician_response
not reviewed

如此未來換 AI model：

Clinical logic 不需要全部重做。

---

# 31. Calculator Result Object

例如：

### calculator
KFRE

### version
4-variable

### inputs
Age  
Sex  
eGFR  
UACR

### result
2-year risk = XX%

5-year risk = XX%

### interpretation
High risk

### action
Review nephrology referral

所有 calculator 都採同樣 data model。

---

# 32. Alert 分級

避免 alert fatigue。

## Level 1 — Information

只在 widget 顯示。

例如：

> PREVENT XX%

---

## Level 2 — Clinical Attention

Highlight。

例如：

> FIB-4 elevated

> Foot screening overdue

---

## Level 3 — Safety Alert

才 pop-up。

例如：

- serious medication contraindication
- severe hypoglycemia risk
- dangerous laboratory result
- major drug interaction
- order conflict

不能所有 guideline gap 都 pop-up。

---

# 33. 醫令變更原則

Agent 可以：

建議。

但不能自行：

- start medication
- stop medication
- change dose
- order procedure

除非醫師確認。

因此：

# Human-in-the-loop

必須是核心設計。

只有在：

[Apply to HIS]

之後，

才進入正式醫令確認畫面。

---

# 34. Clinical Logic 與 LLM 必須分離

整體最好是：

## Deterministic Calculator

FIB-4  
WATCH-DM  
PREVENT  
KFRE  
ABI/TBI  
IWGDF

↓

## Rule Engine

ADA  
KDIGO  
IWGDF  
Taiwan guideline  
P4P

↓

## OpenClaw Agent

負責：

- 整合
- 解釋
- prioritization
- summarization
- patient communication

↓

## Physician

最終決策。

LLM 不應自己：

心算 FIB-4

或

「憑記憶」決定 cutoff。

---

# 35. Version Control

所有 Clinical Logic 都必須有版本。

例如：

calculator/FIB4/v1

calculator/WATCH_DM/v1

calculator/PREVENT/v1

calculator/KFRE/v1

rule/ADA_SOC_2026

rule/IWGDF_2023

rule/TW_DM_P4P_2026

因此 2027 guideline 出來時：

更新：

ADA_SOC_2027

而不是修改 AI Prompt 後不知道改了什麼。

---

# 36. Audit Trail

系統必須留下：

Agent 看了什麼資料？

算了什麼？

使用哪一版公式？

引用哪一版 guideline？

產生什麼 recommendation？

醫師有沒有接受？

最後有沒有執行？

如此未來才可以進行：

- clinical validation
- quality improvement
- research
- regulatory review
- patient safety review

---

# 37. Local Validation

非常重要：

PREVENT、WATCH-DM、Karter Hypoglycemia Risk 等模型並非以台灣族群建立。

PREVENT 的原始模型主要來自超過 650 萬名美國成人，因此適合先作 risk communication / risk stratification，但台灣正式進入 decision threshold 前，應進行 local calibration / validation。

同樣：

WATCH-DM  
Hypoglycemia model  
KFRE

都應記錄：

# model provenance

避免將：

「國外 validated prediction」

直接當成：

「台灣病人確定風險」。

---

# 38. 建議 MVP 開發順序

## Phase 1
### Read-only Diabetes Copilot

只做：

- Patient summary
- trend
- complications
- care gap

不寫入醫令。

---

## Phase 2
### Calculator Library

導入：

- CKD G/A
- FIB-4
- WATCH-DM
- NT-proBNP pathway
- ABI/TBI
- PREVENT
- Hypoglycemia risk
- IWGDF
- KFRE

---

## Phase 3
### Guideline Engine

導入：

- ADA
- Taiwan guideline
- KDIGO
- IWGDF
- P4P

開始產生：

Guideline Gap。

---

## Phase 4
### Action Layer

讓醫師可以直接：

- order lab
- referral
- appointment
- medication review
- education

但仍必須 physician confirmation。

---

## Phase 5
### Population Health

Agent 主動掃描：

整個 Diabetes Registry。

產生：

Priority Patient List。

---

# 39. 第一版真正值得優先做的六件事情

如果資源有限，我會優先：

### 1. Patient Summary + Trend

讓醫師少翻病歷。

### 2. Complication Map

把腎、眼、神經、足、ASCVD/PAD 整合。

### 3. Care Gap / P4P

直接回答：

> 今天缺什麼？

### 4. FIB-4 + WATCH-DM + PREVENT + KFRE

建立 Advanced Risk Calculator。

### 5. Medication Guideline Gap

回答：

> 有沒有 evidence-based treatment 漏掉？

### 6. Patient Education

把醫師的 clinical plan 自動轉換成病人看得懂的繁體中文。

---

# 40. 最終門診畫面範例

## Diabetes Copilot

**65-year-old male / T2DM 12 years**

### Current status

HbA1c  
7.2 → 7.5 → 8.1%

Weight  
+4.2 kg / year

LDL  
82 mg/dL

eGFR  
68 → 59 → 51

UACR  
42 → 86 → 176

---

### Confirmed complications

🔴 CKD G3aA2

🔴 Previous ischemic stroke

---

### Screening status

🟢 Retinopathy  
Last screening negative

🟡 Foot  
Screening overdue

---

### Advanced Risk

🟡 WATCH-DM  
High HF risk

🟡 NT-proBNP  
Not tested

🟡 FIB-4  
1.56 → secondary fibrosis assessment suggested

🔴 KFRE  
Elevated 5-year kidney failure risk

🔴 Hypoglycemia Risk  
High

🟡 IWGDF Foot Risk  
Category 1

PREVENT：
Not applicable because established ASCVD

---

### Guideline Review

⚠ CKD with albuminuria

Review kidney-protective therapy.

⚠ Previous stroke

Review secondary ASCVD prevention.

⚠ High hypoglycemia risk

Review insulin/SU treatment and glycemic target.

---

# Suggested Actions Today

□ Review medication

□ Foot assessment

□ NT-proBNP

□ FibroScan / VCTE

□ Review renal progression

□ Generate patient report

---

# 41. OpenClaw for Diabetes HIS 的真正定位

最終這個系統不是：

# AI Chatbot

而是：

# Diabetes Clinical Operating Agent

它持續完成：

**讀取病歷**

↓

**建立病人狀態**

↓

**辨識併發症**

↓

**計算未來風險**

↓

**尋找 Care Gap**

↓

**對照 Guideline**

↓

**提出下一步**

↓

**等待醫師確認**

↓

**協助完成醫令與衛教**

↓

**追蹤是否完成**

↓

**再回到下一次照護循環**

最後真正希望達到的是：

# 「不是再給醫師更多資料，而是把資料轉換成當下可以執行的臨床行動。」

這應該就是 OpenClaw for Diabetes HIS 與一般 AI Chatbot 或 Dashboard 最大的差別。