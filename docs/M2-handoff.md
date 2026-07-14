# M2 交接 — 真雲端一條龍 pipeline（分析 + 渲染）

> 本輪把「浪 LIVE」從 in-memory/stub 升級成**每一段都接真的 AWS pipeline**：
> 真上傳 → 真 Amazon Transcribe → highlights(+Bedrock) → compose → 編輯器草稿 →
> 真 FFmpeg on AWS Batch → 下載。權威願景見 `docs/demand.md`。

## 已完成（程式碼 + Terraform 皆已進 repo，離線驗證通過）

| 里程碑 | 內容 | 狀態 |
|---|---|---|
| **M2.0** | 控制面變真：backend Lambda 加 `USE_INMEMORY=0` + 真實 table/bucket 名 env + S3/DynamoDB IAM（修掉「跑 in-memory」與資源名不一致） | ✅ 已寫、`terraform validate` 過 |
| **M2.1** | 分析面：`workers/lambda_handlers.py`（薄 handler 包純函式 worker）、`analysis/highlights_llm.py`（Bedrock gated 加強）＋ `analysis-workflow`/`analysis-ingress` 模組（S3→EventBridge→SQS→Starter→Step Functions，真 Transcribe） | ✅ handler 鏈離線跑到 READY_TO_EDIT、validate 過 |
| **M2.2** | 渲染面：`render_worker.py` Encoder seam（Stub/**FFmpeg**）、`workers/render/`＋`Dockerfile.render`（真一次 pass ffmpeg）、`app/aws/orchestration.py`、`POST /renders`→StartExecution ＋ `render-ecr`/`render-batch`(Fargate)/`render-workflow` 模組 | ✅ 真 ffmpeg 全鏈跑到 ARTIFACT_READY、validate 過 |
| **M2.3** | 契約補洞：openapi 升 **0.3.0**（`upload-session/complete` 端點 + Cognito JWT scheme）＋ 後端 `complete_multipart_upload`；前端接真（Cognito 登入、multipart 完成、render、下載、輪詢） | ✅ 後端/契約完成；前端由 agent 實作 |
| **M2.4** | `deploy.sh` 改寫為 Lambda+Batch 兩 image 流程；`infra/README.md` 更新（遠端 state + M2 模組） | ✅ |

**離線驗證已通過**：`backend-api` pytest 全綠；分析 handler 鏈 → READY_TO_EDIT；真 ffmpeg 一次 pass（裁切+串接+9:16 pad+字幕+loudnorm）產出合法 MP4；全渲染鏈 plan→真 ffmpeg→ARTIFACT_READY；`terraform validate` 全過。

---

## 需要「真憑證」才能做的步驟（本機無 AWS 憑證，交接給你）

> 憑證用臨時 token 寫進 scratchpad env 檔 `source`，**絕不進 repo**。全程 region `us-east-1`。

### 1）逐一 SCP probe（每個新服務先 create→delete / list，確認 workshop 沒擋）
```bash
# Transcribe
aws transcribe list-transcription-jobs --max-results 1          # 應 200
# Bedrock（另需在 console 開通 Nova 模型存取，與 IAM/SCP 分開！）
aws bedrock list-foundation-models --region us-east-1 --query 'modelSummaries[?contains(modelId,`nova-micro`)]'
# SQS
Q=$(aws sqs create-queue --queue-name scp-probe --query QueueUrl --output text) && aws sqs delete-queue --queue-url "$Q"
# EventBridge
aws events put-rule --name scp-probe --event-pattern '{"source":["aws.s3"]}' && aws events delete-rule --name scp-probe
# Step Functions（list 可，create 於 apply 時驗）
aws stepfunctions list-state-machines --max-results 1
# AWS Batch（風險最高：底層 EC2/ECS/Fargate 可能被擋）
aws batch describe-compute-environments --max-results 1
```
**若 Batch/Fargate 被擋** → 依 `modules/render-batch/main.tf` 頂註退路：Batch EC2 → 最終 ffmpeg-in-Lambda（短片可行）。

### 2）部署（`infra/deploy.sh`，會先 plan 給你看再 apply）
```bash
cd infra && ./deploy.sh
# 內部：init → 建兩個 ECR → build/push backend:lambda + render:render →
#       terraform plan（審 IAM/Batch）→ apply（帶 backend_lambda_image + render_image）→
#       前端 build（帶 NEXT_PUBLIC_API_BASE_URL + NEXT_PUBLIC_COGNITO_*）→ s3 sync → CloudFront invalidation
```
> apply **一律**帶 `-var backend_lambda_image=…:lambda`（deploy.sh 已內建），否則後端會還原成 placeholder。

### 3）端到端驗證（真雲端）
```bash
D=infra/environments/dev
# a. 分析面：presigned 上傳一支「短」mp4（demo 用短片；Transcribe 約 0.5–1x 實時、~$0.024/min）
#    建 project → upload-session → PUT parts → upload-session/complete
#    然後觀測自動走：UPLOADING → ANALYZING → COMPOSING → READY_TO_EDIT
curl -s "$(terraform -chdir=$D output -raw backend_api_endpoint)/projects/<id>" | jq .status
terraform -chdir=$D output -raw analysis_state_machine_arn   # 到 Step Functions console 看執行
# b. 渲染面：前端按 Render（或 POST /renders）→ 追 render SFN + Batch job
curl -s "$(terraform -chdir=$D output -raw backend_api_endpoint)/renders/<render_id>" | jq '.status,.current_stage'
# 到 ARTIFACT_READY 後 GET /artifacts/<id>/download → 開簽章 URL 下載真 final.mp4（可播）
# c. 前端整合：開 CloudFront 網址 → 登入 → 上傳 → 等草稿 → 編輯 → Render → 下載
terraform -chdir=$D output -raw cloudfront_domain
```

---

## 設計要點（新 session / reviewer 先看）
- **零改 worker 邏輯**：分析/渲染 Lambda 與 Batch 容器都只是薄 entrypoint，呼叫同一批純函式 worker（`analysis_worker`/`composer_worker`/`creative_worker`/`render_worker.run`）。演算法不變。
- **一顆 backend image、多個 Lambda**：worker Lambda 用 `image_config.command` 覆寫 CMD 指到 `workers.lambda_handlers.<name>`（AWS Lambda base image + RIC）。FFmpeg 用**另一顆** image（`Dockerfile.render`，apt ffmpeg）跑在 Batch。
- **Encoder seam**：`render_worker.get_encoder()` 預設 `StubEncoder`（離線/pytest 不變）；Batch 容器設 `RENDER_ENCODER=ffmpeg` 才切真 `FFmpegEncoder`。
- **控制面不跑重活**：`POST /renders` 在 `RENDER_STATE_MACHINE_ARN` 有設時只寫 render item + StartExecution（async）；未設時退回 inline shim（讓 pytest/CLI 可跑）。`POST /compose` 維持 inline（純排序，對真 DynamoDB）。
- **冪等**：S3 事件 at-least-once → Starter 以 `project_id+version_id` 造確定性 SFN execution name，重複事件吞 `ExecutionAlreadyExists`。
- **Bedrock gated**：`HIGHLIGHT_LLM_ENRICH=1`（detect_highlights worker 預設開）才呼叫 Bedrock 補標題/理由，且 fail-open（Bedrock 掛不影響 pipeline）。
- **Raw bucket CORS**：`storage-editor` 已對 raw bucket 開 CORS（`PUT/GET/HEAD`、`ExposeHeaders: ETag`、origin `*`——presigned 簽章才是安全邊界），讓瀏覽器直傳 + 讀 ETag 完成 multipart。work/output 不需要（僅伺服器端存取）。

## 已知風險 / 待辦
- **AWS Batch (Fargate) SCP** 為最大不確定點 → 先 probe，退路見上。
- **Transcribe** 用 blocking Lambda（15 分上限）；長片改 SFN 輪詢或 waitForTaskToken（`docs/speaker-attribution/attribute-speakers.asl.json` 有範本）。
- **API Gateway JWT authorizer**（硬性驗證）本輪未強制掛（避免 demo 破）；前端做真 Cognito 登入 + 帶 Bearer、後端 app 層寬鬆接受。要硬性強制時再於 `backend-lambda` 加 `aws_apigatewayv2_authorizer`（注意 `$default` proxy route 是 all-or-nothing，`/health` 會一起被擋）。
- **FFmpeg effects**：一次 pass 已含 裁切/串接/比例/字幕/loudnorm；`effects.v1`（zoom/flash）已凍結進 artifact 但尚未套進 filtergraph（後續）。`effect_seed` 仍可重現。
- **PR #24（BitDetector seam）** 未合，非關鍵路徑（下游仍走 highlights.v1）；本輪未動 GitHub issue（依指示）。
