# ROADMAP — infra（分析／渲染／儲存交付面・AWS 編排）

> 權威願景：[`../docs/demand.md`](../docs/demand.md)。本檔只列**目標**與**原則**，不含實作細節；逐步推進，最後與 frontend-web／backend-api 整合。

## 目標（里程碑）
- **M1 儲存與身分基礎**：三 bucket `video-editor-{raw,work,output}-{env}`（key layout 依 demand.md §十六）＋ DynamoDB 單表 `VideoEditor`（PK/SK 依 §十七）＋ Cognito user pool；沿用現有 CloudFront 前端交付。
- **M2 事件入口與分析編排**：EventBridge（raw `source/` ObjectCreated）→ SQS `analysis-intake`（＋DLQ）→ Starter Lambda（冪等）→ Step Functions「Analysis & Composition Workflow」（Validate→Probe→Transcribe→Normalize→DetectHighlights→ComposeTimeline→SaveDraft→`READY_TO_EDIT`），**不等使用者**、出草稿即結束。
- **M3 渲染編排**：Step Functions「Render Workflow」（ValidateTimeline→SubtitlePlan→EffectPlan→BuildSpec→Batch FFmpeg `.sync`→Validate→Thumbnail/Manifest→Publish）；AWS Batch（EC2）＋ ECR 版本化 FFmpeg image。
- **M4 分流與韌性**：三 queue（`analysis-intake`／`ai-task`／`render-job`）、per-worker 最小 IAM、遠端 Terraform state、CloudWatch 觀測與告警。

## 原則（infra 專屬）
- 資源命名與契約／schema 對齊；**輸入與輸出不同 bucket**；S3 事件至少一次投遞 → Starter 以 `bucket+key+version_id` 冪等。
- **控制面／資料面分離**：HTTP API 服務（App Runner 若帳號可用；否則 ECS Express Mode；**本 workshop 帳號 App Runner 被 SCP 擋，現以 Lambda ＋ API Gateway 替代**）只做控制；worker 跑非同步重活。
- Step Functions **不卡等使用者**（分析出草稿即結束；使用者可能隔天才回來編輯）。
- 每加一個新服務先小 **probe** 確認 SCP 允許（本帳號已知擋 App Runner 與公開 Lambda Function URL）。

---

## 一致性原則（三 worktree 共同遵守，不可各自變更）
1. **契約即法**：`contracts/` 是唯一介面真相來源（`transcript.v1`、`highlights.v1`、`timeline.v1`、`subtitle.v1`、`effects.v1`、`artifact.v1`、`render_spec.v1`、`openapi.yaml`）。任何欄位或語意變更＝跨組協調：先改 `contracts/`、升 `schema_version`、推 `main`、通知另外兩組；不得在單一 worktree 私自偏離。
2. **資料模型**：核心實體是 **Project**（非 job）；時間單位一律 **毫秒（ms）**；識別碼命名固定：`project_id`／`highlight_id`／`timeline_version`／`render_id`／`artifact_id`／`tenant_id`。
3. **狀態機為共同語言**：Project 與 Render 狀態列舉依 `docs/demand.md §十八`；前端據此顯示、後端據此轉移、infra 據此編排；新增狀態必須三方同步。
4. **儲存契約**：DynamoDB 單表 `VideoEditor`（PK/SK 依 demand.md §十七）；S3 三 bucket（raw/work/output）key layout 依 demand.md §十六；輸入與輸出不同 bucket。
5. **API 契約**：前後端互動只透過 `contracts/openapi.yaml`；後端實作對齊契約，前端不假設未定義行為。
6. **邊界**：控制面（HTTP API）只做互動與工作提交，不跑長時 FFmpeg；重活由非同步 worker 處理，需冪等、可重試；Timeline 版本只新增不覆蓋；`effect_seed` 固定以確保 Render 可重現。
7. **協作紀律**：各自只改自己的目錄；動工前先 `git fetch && git merge origin/main`；commit 引用對應 issue；`contracts/` 變更立即推 `main` 並知會另外兩組。
8. **可驗證**：每階段各自能獨立驗證（前端 `npm run build`、後端 `pytest`、infra `terraform validate`），並定期跑跨組端到端 smoke。
9. **契約現況**：`contracts/` 目前為 M0（job／秒）版；M1 第一步由**後端主責、三方確認**，升級為 demand.md 的 Project／毫秒版。升級落地前，一律以 `docs/demand.md` 為準。
