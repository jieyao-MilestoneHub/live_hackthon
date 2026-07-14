# ROADMAP — 浪 LIVE AI 直播高光剪輯

> 從直播/影片自動辨識高光時刻、產出精華短片的近即時非同步系統。
> 架構設計：[`docs/aws-infra.md`](docs/aws-infra.md)　·　任務追蹤：[GitHub Issues](https://github.com/jieyao-MilestoneHub/live_hackthon/issues)（22 issue，milestone `MVP (Phase 1)`）

---

## 1. 願景與架構

使用者上傳影片後，系統於數秒內啟動 pipeline：轉逐字稿 → 分析高光 → 裁切短片 → 回存並提供下載。核心資料流：

```
上傳 → S3(raw) → EventBridge → SQS → Starter Lambda（冪等）
   → Step Functions（Transcribe → 正規化 transcript.v1 → 分析 highlights.v1
                      → Map 裁切每個片段 → 產 manifest）
   → S3(output) → CloudFront 簽章 URL 下載
狀態：DynamoDB VideoJobs 單表（job / stage / clip items）
```

本專案採「上傳完成後才處理」的近即時非同步設計（非直播逐段收流）。

## 2. 技術棧與佈局

| 層 | 技術 | 部署目標 |
|---|---|---|
| 前端 `frontend-web/` | Next.js 靜態匯出 | S3 + CloudFront |
| 後端 `backend-api/` | FastAPI + 分析模組 | ECR → App Runner |
| Infra `infra/` | Terraform（ap-northeast-1） | AWS dev/staging/prod |
| 契約 `contracts/` | JSON Schema + OpenAPI | 跨組共用 |

## 3. 介面契約（真相來源＝ `contracts/`）

| 契約 | 檔案 | 對應 issue | 擁有者 → 消費者 |
|---|---|---|---|
| 逐字稿 | `transcript.v1.schema.json` | #4 | 後端 S4 → 分析 |
| 高光 | `highlights.v1.schema.json` | #6 | 分析 → 系統裁切 |
| Job API | `openapi.yaml` | — | 後端 → 前端 |

**規則**：契約是跨組邊界，任何欄位/語意變更須三組同步並升 `schema_version`（`transcript.v2` …）。

## 4. 里程碑

### M0 — 走路骨架（本回合 ✅ 目標）
- monorepo、`contracts/`、`ROADMAP.md`、3 個 worktree 就緒。
- 前端（上傳/狀態/結果頁）＋ 後端（`/health` + Job API stub）＋ 分析模組（規則式 + 測試）scaffold 完成。
- **前後端實際部署到 AWS dev**：CloudFront 開得出前端、App Runner `/health` 回 200、前端能打到後端。

### M1 — MVP Pipeline（對應 aws-infra.md wave 0–2）
| Wave | Issues | 產出 |
|---|---|---|
| 0 | #4 #6 #8 #9 | schema 契約、架構、S3/IAM、Terraform 基礎 |
| 1 | #10 #11 #5 | EventBridge+SQS+Starter、Transcribe→transcript.v1、分析邏輯 |
| 2 | #12 #7 | 分析串接、離線驗證 |

### M2 — 裁切與 E2E
- #13 裁切產短片（MVP：本機/Lambda ffmpeg；規模化：AWS Batch + FFmpeg 容器）。
- #14 端到端整合測試 + 失敗情境（重複事件、Transcribe 失敗、ffmpeg 非零、併發、DLQ redrive）+ 壓測。

### M3 — 交付物（簡報組）
- #15 流程圖、#16 簡報、#17 成效指標、#18 成果截圖、#19 測試紀錄、#20 Demo 腳本、#21 備援錄影。

### Phase 2 — 後續優化（#22）
- 多模態融合（彈幕情緒、Rekognition 視覺動態）、多平台風格產出（TikTok/Reels/Shorts 直式）、內容審核合規、批次處理、CloudFront 簽章下載、Step Functions callback + reconciliation。

## 5. Worktree / 分支協作模型

```
main（可部署基座）
├─ feat/frontend-web → ../live_hackthon-frontend-web （前端組）
├─ feat/backend-api  → ../live_hackthon-backend-api  （後端＋分析組）
└─ feat/infra        → ../live_hackthon-infra         （系統/infra 組）
```
各 worktree 含整包 repo 但只改自己目錄；PR 合併回 `main`。commit 引用 issue 編號。

## 6. 環境

`dev`（本回合）→ `staging` → `prod`。資源命名 `*-{env}`；憑證用 GitHub Actions OIDC（不存長期金鑰），本機部署用 `aws configure`。

## 7. 部署 runbook（摘要，細節見 `infra/deploy.sh`）

- **後端**：`docker build` → ECR push → `terraform apply`（App Runner 讀新 image）→ 取得 service URL。
- **前端**：設 `NEXT_PUBLIC_API_BASE_URL`=App Runner URL → `npm run build` → `aws s3 sync out/ s3://<bucket>` → CloudFront invalidation。

## 8. 成效指標（取自 aws-infra.md §13）

Upload→pipeline-start p50/p95、E2E 成功率與延遲、處理時間/影片長度、Transcribe/分析/FFmpeg 各段耗時、佇列積壓、重試率、DLQ 數、每分鐘處理成本、每 job 產出短片數、空高光率。

**Demo 劇本**：一支正常影片、一支無明顯高光、一支故意造成分析失敗、2–5 支同時上傳。
