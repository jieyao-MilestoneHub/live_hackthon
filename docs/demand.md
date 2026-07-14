先假設「使用者指定秒數」是指 **最終短片長度，最多 60 秒**，原始影片可以超過一分鐘。

另外，App Runner 現已不開放新 AWS 客戶使用；既有 App Runner 客戶仍可繼續建立服務。若你們帳號已能使用，就按照以下設計；新帳號則將圖中的 App Runner 替換成 ECS Express Mode。([AWS 文件][1])

---

# 一、重新定義完整使用流程

這次不應該是「上傳後一路自動跑到底」，而應該分成兩段：

## 第一段：分析並建立初始剪輯草稿

```text
使用者進入影片編輯器
        ↓
建立 Project，指定目標秒數，例如 30 秒
        ↓
取得 Presigned Upload URL
        ↓
瀏覽器直接上傳原始影片至 S3
        ↓
S3 完成上傳
        ↓
觸發分析 Pipeline
        ↓
產出多個高光候選片段
        ↓
Composer Worker 根據目標秒數建立初始 Timeline
        ↓
編輯器顯示高光片段與自動剪輯草稿
```

## 第二段：使用者確認並產生 Artifact

```text
使用者在編輯器中：
調整順序／刪除片段／鎖定片段
選擇字幕與特效設定
        ↓
提交 Render
        ↓
AI 產生字幕與特效計畫
        ↓
FFmpeg 依 Timeline 一次完成：
裁切、串接、轉場、字幕、特效、音訊處理
        ↓
輸出 Final Artifact
        ↓
使用者取得 Signed Download URL
```

這樣使用者才真的有一個「編輯區」，而不是只有上傳與等待頁面。

---

# 二、核心架構設計

整體分成四個平面：

| 平面     | 主要功能                              | AWS 服務                                   |
| ------ | --------------------------------- | ---------------------------------------- |
| 編輯器控制面 | 前端、登入、Project、Timeline、Render API | CloudFront、Cognito、App Runner            |
| 分析處理面  | 轉錄、高光分析、初始組片                      | Step Functions、Lambda、Bedrock、Transcribe |
| 重型渲染面  | FFmpeg 裁切、特效、字幕、輸出                | AWS Batch、EC2、ECR                        |
| 儲存與交付面 | 原始檔、中間結果、Artifact、下載              | S3、DynamoDB、CloudFront                   |

App Runner 只負責 HTTP 與編輯器控制，不負責長時間執行 FFmpeg。

---

# 三、專業架構圖配置

建議整張圖採取：

```text
由左至右：使用者 → 編輯器 → 上傳 → 分析 → 編輯 → 渲染 → Artifact
```

上半部畫「同步使用者操作」，下半部畫「非同步 Worker Pipeline」。

---

## 區塊 1：使用者與編輯器

最左側放：

```text
Content Creator
Web Browser
```

右側放：

```text
Amazon CloudFront
AWS WAF
```

後面放兩個應用程式元件。

### Frontend

推薦靜態 SPA：

```text
Frontend Editor
React / Vue / Next.js Static Export

Amazon S3
+
Amazon CloudFront
```

若使用 Next.js SSR，也可以畫成：

```text
AWS App Runner
Frontend Editor Service
```

前端編輯器功能寫在框內：

```text
Video Preview
Target Duration ≤ 60 sec
Highlight Candidate List
Timeline Editor
Subtitle Settings
Effect Settings
Render Progress
Artifact Download
```

### Authentication

編輯器旁放：

```text
Amazon Cognito
User Authentication
```

連線：

```text
Browser → Cognito
Browser → App Runner API
```

---

# 四、App Runner Backend

Frontend 右側放：

```text
AWS App Runner
Editor Backend API
```

框內列出 API：

```text
POST /projects
POST /projects/{id}/upload-session
GET  /projects/{id}
GET  /projects/{id}/highlights
GET  /projects/{id}/timeline
PUT  /projects/{id}/timeline
POST /projects/{id}/compose
POST /projects/{id}/renders
GET  /renders/{render_id}
GET  /artifacts/{artifact_id}/download
```

App Runner 的職責：

```text
驗證 Cognito JWT
建立 Project
保存目標秒數
產生 Presigned Upload URL
讀取高光候選
儲存 Timeline 編輯結果
提交 Render 工作
回傳處理狀態
產生 Signed Download URL
```

App Runner 連到：

```text
Amazon DynamoDB
VideoEditor Table
```

以及：

```text
Amazon S3
Raw / Work / Output Buckets
```

---

# 五、正確上傳順序

架構圖中要明確畫成：

```text
Browser
   │
   │ POST /projects
   ▼
App Runner Backend
   │
   │ 建立 project_id
   │ 建立 upload_id
   │ 分配 S3 object key
   ▼
DynamoDB
```

接著：

```text
Browser
   │
   │ POST /projects/{id}/upload-session
   ▼
App Runner Backend
   │
   │ Presigned multipart upload URLs
   ▼
Browser
   │
   │ Direct Multipart Upload
   ▼
S3 Raw Video Bucket
```

需要避免寫成模糊的：

```text
Get S3 URL
```

應明確寫：

```text
Generate Presigned Upload Authorization
```

上傳完成後，才產生真正的 S3 object：

```text
tenant={tenant_id}/
project={project_id}/
source/
source.mp4
```

---

# 六、事件入口

Raw Bucket 右側依序放：

```text
Amazon EventBridge
S3 Object Created
```

```text
Amazon SQS
Analysis Intake Queue
```

```text
AWS Lambda
Analysis Pipeline Starter
```

```text
AWS Step Functions Standard
Analysis Workflow
```

完整箭頭：

```text
S3 Raw Bucket
   │ Object Created
   ▼
EventBridge
   │ Route Upload Event
   ▼
SQS Analysis Intake
   │ Buffered Processing
   ▼
Pipeline Starter Lambda
   │ StartExecution
   ▼
Analysis Step Functions
```

S3 事件採至少一次投遞，因此同一個上傳事件可能重複出現；Pipeline Starter 從 key 解析出 `project_id`，並以 `project_id + version_id` 造確定性 SFN execution name 做冪等（重複事件因 `ExecutionAlreadyExists` 被吞掉，收斂為單一次執行）。此鍵在語意上等同 `bucket + key + version_id`：bucket 固定、`key ↔ project_id` 一對一（`tenant=/project=/source/`）。raw bucket 已開 versioning，故每次重新上傳都帶新的 `version_id` → 新 execution name → 觸發新的分析。([AWS 文件][2])

SQS 下方另外畫：

```text
Analysis Intake DLQ
```

---

# 七、第一條 Pipeline：分析與自動組片

第一個 Step Functions 建議命名：

```text
Video Analysis and Composition Workflow
```

內部流程：

```text
Validate Source
       ↓
Probe Video Metadata
       ↓
Start Transcription
       ↓
Normalize Transcript
       ↓
Detect Highlights
       ↓
Compose Initial Timeline
       ↓
Save Editor Draft
       ↓
Mark Project READY_TO_EDIT
```

不要讓這條 workflow 等待使用者編輯。

它產出草稿後就結束，專案狀態改成：

```text
READY_TO_EDIT
```

使用者可能隔幾分鐘甚至隔天才回來，因此不應該讓 Step Functions 一直卡在等待使用者。

---

# 八、Analysis Worker

在 Step Functions 下方畫第一條 Worker Lane：

```text
Highlight Analysis Worker
Light / AI Workload
```

推薦 MVP：

```text
SQS Analysis Task Queue
        ↓
AWS Lambda Analysis Worker
        ↓
Amazon Bedrock
```

Analysis Worker 輸入：

```text
transcript.v1.json
video metadata
target duration
analysis parameters
```

輸出：

```text
highlights.v1.json
```

內容例如：

```json
{
  "schema_version": "highlights.v1",
  "project_id": "project-123",
  "source_duration_ms": 1200000,
  "highlights": [
    {
      "highlight_id": "hl-001",
      "start_ms": 15200,
      "end_ms": 31800,
      "score": 0.94,
      "reason": "關鍵產品說明與情緒高點",
      "transcript": "這是本次最重要的功能",
      "suggested_title": "核心功能亮點"
    }
  ]
}
```

若分析使用自有大型模型、GPU 或長時間本機推論，則把 Lambda 替換成：

```text
AWS Batch AI Worker
GPU Compute Environment
```

但只是呼叫 Bedrock 或外部 API 時，Lambda 通常更適合。

---

# 九、Composer Worker

Analysis Worker 後方放第二條 Worker：

```text
Duration Composer Worker
```

它不是直接輸出影片，而是輸出「剪輯決策表」。

輸入：

```text
highlights.v1.json
target_duration_ms
composition rules
locked highlights
excluded highlights
```

輸出：

```text
timeline.v1.json
```

例如使用者要求 30 秒：

```json
{
  "schema_version": "timeline.v1",
  "project_id": "project-123",
  "version": 1,
  "target_duration_ms": 30000,
  "actual_duration_ms": 29800,
  "clips": [
    {
      "timeline_order": 1,
      "highlight_id": "hl-004",
      "source_start_ms": 125000,
      "source_end_ms": 136500,
      "timeline_start_ms": 0,
      "timeline_end_ms": 11500
    },
    {
      "timeline_order": 2,
      "highlight_id": "hl-001",
      "source_start_ms": 15200,
      "source_end_ms": 33500,
      "timeline_start_ms": 11500,
      "timeline_end_ms": 29800
    }
  ]
}
```

建議 Composer 的規則：

```text
最終長度不得超過 60 秒
優先選擇高分片段
避免內容語意重複
避免在句子中間裁切
允許片頭／片尾 Padding
允許使用者鎖定指定片段
允許使用者排除片段
實際長度誤差控制在 ±0.5 秒
```

Composer 可使用：

```text
Lambda
```

若只是排序、合併、最佳化秒數，不需要 FFmpeg。

---

# 十、編輯區資料回流

Composer 完成後：

```text
timeline.v1.json
        ↓
S3 Work Bucket
        ↓
DynamoDB Project Status = READY_TO_EDIT
```

瀏覽器查詢：

```text
GET /projects/{id}
GET /projects/{id}/highlights
GET /projects/{id}/timeline
```

編輯器畫面至少有四個區域：

```text
┌────────────────────────────────────────────────┐
│ Video Preview                                  │
├───────────────────────┬────────────────────────┤
│ Highlight Candidates  │ Project Settings       │
│                       │ Target: 30 sec          │
│ □ Highlight A         │ Subtitle: Auto          │
│ □ Highlight B         │ Effect: Random          │
│ □ Highlight C         │ Aspect: 9:16            │
├───────────────────────┴────────────────────────┤
│ Timeline                                       │
│ [Clip A][Transition][Clip C][Clip B]            │
├────────────────────────────────────────────────┤
│ Save Draft                         Render Video │
└────────────────────────────────────────────────┘
```

使用者可以：

* 拖曳調整順序
* 移除片段
* 修改片段起訖點
* 鎖定某個高光
* 重新執行自動組片
* 指定比例，例如 16:9、9:16 或 1:1
* 開關字幕
* 選擇特效強度

每次修改 Timeline：

```text
PUT /projects/{id}/timeline
```

建立新版本：

```text
timeline version 1
timeline version 2
timeline version 3
```

不要覆蓋舊版本，方便 Undo、重新 Render 及問題追蹤。

---

# 十一、第二條 Pipeline：Render Workflow

使用者按下：

```text
Render Video
```

Frontend 呼叫：

```text
POST /projects/{project_id}/renders
```

App Runner 執行：

```text
1. 驗證 timeline
2. 凍結 timeline_version
3. 建立 render_id
4. 寫入 DynamoDB
5. 啟動 Render Step Functions
```

第二個 workflow：

```text
Video Artifact Render Workflow
```

流程：

```text
Validate Timeline
       ↓
Generate Subtitle Plan
       ↓
Generate Effect Plan
       ↓
Build Render Specification
       ↓
Submit FFmpeg Batch Job
       ↓
Validate Artifact
       ↓
Generate Thumbnail and Manifest
       ↓
Publish Artifact
```

---

# 十二、字幕與特效 AI Worker

渲染前先放一個 Light Worker：

```text
Creative Planning Worker
Lambda + Amazon Bedrock
```

它負責產生：

```text
subtitle.vtt
subtitle_style.json
effect_plan.json
render_spec.json
```

## 字幕輸出

```json
{
  "schema_version": "subtitle.v1",
  "language": "zh-TW",
  "cues": [
    {
      "start_ms": 0,
      "end_ms": 2400,
      "text": "這是本次最重要的功能",
      "emphasis_words": ["最重要", "功能"]
    }
  ]
}
```

## 特效計畫

「隨機特效」不能在每次 Retry 時真的重新隨機，否則同一個 Render 重試會產生不同影片。

因此每次 Render 建立：

```text
effect_seed = 834710
```

特效計畫：

```json
{
  "schema_version": "effects.v1",
  "effect_seed": 834710,
  "effects": [
    {
      "type": "zoom_in",
      "start_ms": 0,
      "end_ms": 1600,
      "strength": 0.08
    },
    {
      "type": "flash_transition",
      "at_ms": 11500,
      "duration_ms": 240
    }
  ]
}
```

重試時沿用相同 `effect_seed`，確保輸出可重現。

---

# 十三、FFmpeg Heavy Worker

Creative Planning Worker 後方放：

```text
AWS Batch
Video Render Job Queue
```

再連到：

```text
AWS Batch
Managed EC2 Compute Environment
```

最後連到：

```text
FFmpeg Render Worker
Amazon EC2 Container
```

旁邊放：

```text
Amazon ECR
Versioned FFmpeg Image
```

Step Functions 可以透過 AWS Batch `.sync` 提交工作並等待完成。([AWS 文件][3])

FFmpeg Worker 輸入：

```text
source.mp4
timeline.v1.json
subtitle.vtt
effect_plan.json
render_spec.json
```

FFmpeg Worker 執行：

```text
裁切來源片段
串接 Timeline
加入轉場
加入動態字幕
加入隨機特效
音量正規化
調整畫面比例
編碼輸出
```

輸出：

```text
final.mp4
thumbnail.jpg
preview.mp4
render.log
```

---

# 十四、不要先組片再重新編碼一次

從業務流程看起來是：

```text
組片
  ↓
加字幕與特效
```

但實際 FFmpeg 實作不建議：

```text
第一次 FFmpeg：產生 combined.mp4
第二次 FFmpeg：加入字幕和特效
第三次 FFmpeg：輸出最終檔
```

這會：

* 增加處理時間
* 增加 Batch 成本
* 造成多次有損編碼
* 增加中間檔空間

推薦做法：

```text
Composer Worker
只輸出 Timeline / EDL
        ↓
Creative Worker
輸出 Subtitle / Effect Plan
        ↓
FFmpeg Worker
一次完成剪接、字幕、特效與編碼
```

邏輯上仍然是「先決定如何組片，再套用字幕與特效」，但只做一次主要 Render。

---

# 十五、Artifact 定義

最終 Artifact 不應只有一支 MP4。

建議輸出：

```text
artifact/
├── final.mp4
├── preview.mp4
├── thumbnail.jpg
├── subtitle.vtt
├── timeline.json
├── render-spec.json
└── manifest.json
```

`manifest.json`：

```json
{
  "schema_version": "artifact.v1",
  "artifact_id": "artifact-789",
  "project_id": "project-123",
  "render_id": "render-456",
  "timeline_version": 3,
  "status": "READY",
  "duration_ms": 29800,
  "aspect_ratio": "9:16",
  "resolution": {
    "width": 1080,
    "height": 1920
  },
  "files": {
    "video_key": "artifacts/artifact-789/final.mp4",
    "preview_key": "artifacts/artifact-789/preview.mp4",
    "thumbnail_key": "artifacts/artifact-789/thumbnail.jpg",
    "subtitle_key": "artifacts/artifact-789/subtitle.vtt"
  },
  "created_at": "2026-07-14T12:30:00Z"
}
```

---

# 十六、S3 儲存配置

建議三個 bucket。

## Raw Bucket

```text
video-editor-raw-{env}/
  tenant={tenant_id}/
    project={project_id}/
      source/
        source.mp4
      upload/
        metadata.json
```

## Work Bucket

```text
video-editor-work-{env}/
  tenant={tenant_id}/
    project={project_id}/
      transcript/
        raw.json
        transcript.v1.json

      analysis/
        highlights.v1.json

      timelines/
        version=1/timeline.json
        version=2/timeline.json
        version=3/timeline.json

      renders/
        render={render_id}/
          subtitle.vtt
          subtitle-style.json
          effect-plan.json
          render-spec.json
          ffmpeg.log
```

## Output Bucket

```text
video-editor-output-{env}/
  tenant={tenant_id}/
    project={project_id}/
      artifacts/
        artifact={artifact_id}/
          final.mp4
          preview.mp4
          thumbnail.jpg
          subtitle.vtt
          manifest.json
```

輸入和輸出使用不同 bucket，可避免輸出檔再次觸發輸入 pipeline；AWS 也建議使用不同 bucket，或至少嚴格限制事件 prefix，以免形成事件循環。([AWS 文件][2])

---

# 十七、DynamoDB Table Schema

建議使用：

```text
VideoEditor
```

主鍵：

```text
PK
SK
```

## Project

```text
PK = PROJECT#{project_id}
SK = META
```

主要欄位：

```text
tenant_id
user_id
title
status
target_duration_ms
source_bucket
source_key
source_version_id
source_duration_ms
latest_timeline_version
latest_render_id
latest_artifact_id
created_at
updated_at
version
```

## Highlight

```text
PK = PROJECT#{project_id}
SK = HIGHLIGHT#{highlight_id}
```

欄位：

```text
start_ms
end_ms
score
reason
transcript
suggested_title
selected
locked
```

## Timeline

```text
PK = PROJECT#{project_id}
SK = TIMELINE#VERSION#{version}
```

欄位：

```text
target_duration_ms
actual_duration_ms
clips
subtitle_settings
effect_settings
aspect_ratio
created_by
created_at
```

## Render Job

```text
PK = PROJECT#{project_id}
SK = RENDER#{render_id}
```

欄位：

```text
timeline_version
status
current_stage
effect_seed
batch_job_id
render_spec_key
artifact_id
error_code
error_message
created_at
started_at
completed_at
```

## Artifact

```text
PK = PROJECT#{project_id}
SK = ARTIFACT#{artifact_id}
```

欄位：

```text
render_id
video_key
preview_key
thumbnail_key
manifest_key
duration_ms
size_bytes
checksum
created_at
```

---

# 十八、狀態設計

Project 狀態：

```text
CREATED
UPLOAD_PENDING
UPLOADING
ANALYZING
COMPOSING
READY_TO_EDIT
RENDER_REQUESTED
RENDERING
ARTIFACT_READY
FAILED
```

Render 狀態：

```text
CREATED
PLANNING_SUBTITLES
PLANNING_EFFECTS
QUEUED
RENDERING
VALIDATING
PUBLISHING
SUCCEEDED
FAILED
```

Frontend 根據這些狀態顯示：

```text
正在上傳
正在分析高光
正在建立初始剪輯
可以開始編輯
正在產生字幕與特效
正在渲染影片
影片已完成
```

MVP 可以每 2–5 秒呼叫：

```text
GET /projects/{id}
```

正式版可增加 API Gateway WebSocket 或 AppSync Subscription 推送進度。

---

# 十九、高併發分流

建議建立三個主要 Queue：

| Queue             | Worker           | 工作          |
| ----------------- | ---------------- | ----------- |
| `analysis-intake` | Starter Lambda   | 上傳事件與分析流程   |
| `ai-task`         | Lambda／AI Worker | 高光、字幕、特效計畫  |
| `render-job`      | AWS Batch        | FFmpeg 重型渲染 |

不要讓 App Runner 處理 Queue backlog。

App Runner 的角色是：

```text
Control Plane
HTTP API
Editor State
Job Submission
Status Query
```

Worker 的角色是：

```text
Data Plane
Asynchronous Processing
CPU / GPU Work
Retries
Backpressure
```

---

> **App Runner 負責互動式編輯器與工作控制；Step Functions、Lambda 與 AWS Batch 負責可擴展的非同步影片分析與渲染。**

[1]: https://docs.aws.amazon.com/apprunner/latest/dg/apprunner-availability-change.html "AWS App Runner availability change - AWS App Runner"
[2]: https://docs.aws.amazon.com/AmazonS3/latest/userguide/EventNotifications.html "Amazon S3 Event Notifications - Amazon Simple Storage Service"
[3]: https://docs.aws.amazon.com/step-functions/latest/dg/connect-batch.html "Run AWS Batch workloads with Step Functions - AWS Step Functions"
