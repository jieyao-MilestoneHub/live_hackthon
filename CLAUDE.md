# CLAUDE.md — 浪 LIVE 專案規範（所有 session 一律遵守）

> AI 直播高光偵測 + 剪輯短片編輯器。架構見 `docs/live_hackathon_arch.png`；里程碑交接見 `docs/M2-handoff.md`。

## 分支模型：只走 `main`
- **只在 `main` branch 上工作**。不開 feature 分支、不用 `develop`、不採 worktree-per-feature 模型。
- 直接提交到 `main`；動工前先 `git fetch && git rebase origin/main`（或 merge）保持同步。
- commit 訊息引用對應 GitHub issue 編號。`contracts/` 有變更立即 push、知會其他組。

## 雲端：唯一環境 = `dev`
- **只有一個 AWS 環境**：account `979287128595`（workshop）、region **`us-east-1`**。不建 staging/prod。
- `infra/environments/` 只保留 `dev`。

## Terraform
- **遠端 state（單一真相）**：S3 `lang-live-tfstate-979287128595`（key `dev/terraform.tfstate`；versioning/BPA/SSE 已開）+ DynamoDB lock table `lang-live-tflock`。
  這兩者是 **aws cli bootstrap、不由 terraform 管理**（避免 chicken-and-egg）。
- 版本：terraform **1.10.5**、pin **`aws ~> 5.0`**。從 main checkout 執行：
  `terraform -chdir=infra/environments/dev init|plan|apply`。
- **apply 前先把 `plan` 給人看**（尤其 IAM 與任何 destroy）。**每加一個新 AWS 服務，先小 probe（create→delete）確認 SCP 允許**再寫進 code。

## Workshop SCP 限制（重要）
- 帳號 SCP **擋 App Runner（`apprunner:*`）與公開 Lambda Function URL**。
- 控制面一律用 **API Gateway HTTP API + Lambda（Mangum 容器）**。S3 / CloudFront / DynamoDB / ECR / Cognito / IAM role 可用。

## AWS 憑證
- 部署用**臨時憑證（會過期）**：寫進 scratchpad 的 env 檔再 `source`，**絕不進 repo**。

## 契約與資料模型
- `contracts/` 為跨組單一真相來源；欄位/語意變更＝先改 contracts → 升 `schema_version` → push → 通知他組。
- 核心實體 **Project**（非 job）；時間單位一律 **毫秒(ms)**；id：`project_id / highlight_id / timeline_version / render_id / artifact_id / tenant_id`。

## 目前部署（dev，供參照）
- 前端：CloudFront `d1cljcvh9h89r.cloudfront.net`（S3 `lang-live-frontend-dev-…`）。
- 後端：API Gateway `3xgcvbiz3j.execute-api.us-east-1.amazonaws.com` → Lambda `lang-live-backend-dev`（image 於 ECR `lang-live-backend-dev:lambda`）。
- 儲存：`lang-live-video-editor-{raw,work,output}-dev-979287128595`。
- 狀態表：DynamoDB `VideoEditor-dev`（PK/SK、GSI1、TTL `expires_at`、PITR）。
- 身分：Cognito user pool `lang-live-editor-dev`（`us-east-1_uaXXJmYwR`）+ web client。
- 尚未建（M2+）：EventBridge → SQS `analysis-intake`(+DLQ) → Starter Lambda → Step Functions → Transcribe / AI workers / AWS Batch(FFmpeg)。
