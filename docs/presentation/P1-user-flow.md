# P1 — 端對端使用者流程圖（#15）

> 驗收：流程涵蓋「輸入直播素材 → AI 分析 → 自動剪輯 → 產出短片」，圖檔可用於簡報 P2。
> 權威來源：`docs/demand.md` §一（兩段式流程）。時間單位一律毫秒（ms）。

## 一句話流程

**上傳直播素材 → AI 自動分析出高光候選並組成初始草稿 →（使用者微調）→ 一次渲染出可下載短片。**

關鍵設計：不是「上傳後一路自動跑到底」，而是**兩段式**——分析先產出「可編輯的草稿」並停在 `READY_TO_EDIT`，使用者回來調整後才觸發渲染。這樣使用者真的有一個「編輯區」，而非只有上傳與等待頁。

---

## 端對端流程圖（可渲染 mermaid）

```mermaid
flowchart TD
    U(["🧑 內容創作者<br/>瀏覽器 SPA"])

    subgraph P1["第一段：分析並建立初始剪輯草稿（不等待使用者）"]
        direction TB
        A1["建立 Project<br/>指定目標秒數 ≤ 60s"]
        A2["取得 Presigned Upload URL"]
        A3[("S3 raw<br/>source/source.mp4")]
        A4["EventBridge<br/>ObjectCreated"]
        A5["SQS analysis-intake<br/>(+DLQ)"]
        A6["Starter Lambda<br/>冪等 StartExecution"]
        A7{{"Step Functions 分析工作流"}}
        A8["ValidateSource → Probe →<br/>Transcribe → DetectHighlights →<br/>Compose → MarkReadyToEdit"]
        A9[("S3 work<br/>transcript / highlights / timeline")]

        A1 --> A2 -->|"瀏覽器直傳 multipart"| A3
        A3 --> A4 --> A5 --> A6 --> A7 --> A8
        A8 -.->|"highlights.v1 / timeline.v1"| A9
    end

    subgraph P2["第二段：使用者確認並產生 Artifact"]
        direction TB
        B1["編輯器：高光候選 + 初始 Timeline"]
        B2["調整順序 / 刪除 / 鎖定<br/>字幕 / 特效 / 畫面比例"]
        B3["提交 Render"]
        B4["Creative Planning<br/>subtitle.vtt · effect_plan · render_spec<br/>(effect_seed 可重現)"]
        B5["AWS Batch + FFmpeg<br/>一次完成裁切/串接/轉場/字幕/特效/編碼"]
        B6[("S3 output<br/>final.mp4 · preview · thumbnail · manifest")]

        B1 --> B2 --> B3 --> B4 --> B5 --> B6
    end

    DDB[("DynamoDB VideoEditor<br/>Project / Highlight / Timeline / Render / Artifact")]
    COG["Cognito 登入 → JWT"]

    U --> COG
    COG --> A1
    A1 -.-> DDB
    A8 -->|"status = READY_TO_EDIT"| DDB
    DDB -->|"輪詢狀態 GET /projects/{id}"| B1
    B2 -.->|"PUT /timeline 版本化 append-only"| DDB
    B6 -->|"Presigned GET 下載"| U
```

---

## 對應到願景四平面

| 平面 | 在流程中的角色 | 主要 AWS 服務（現況） |
|------|----------------|----------------------|
| 編輯器控制面 | 登入、建 Project、上傳授權、Timeline、Render 提交、狀態查詢 | CloudFront · Cognito · API Gateway + Lambda |
| 分析處理面 | 轉錄、高光分析、初始組片 | Step Functions · Lambda · Transcribe ·（Bedrock enrich） |
| 重型渲染面 | FFmpeg 裁切/特效/字幕/輸出 | AWS Batch · ECR · FFmpeg 容器 |
| 儲存與交付面 | 原始檔、中間結果、Artifact、下載 | S3（raw/work/output）· DynamoDB · CloudFront |

## 狀態驅動的畫面提示（demand.md §十八）
`CREATED → UPLOADING → ANALYZING → COMPOSING → READY_TO_EDIT → RENDER_REQUESTED → RENDERING → ARTIFACT_READY`
前端每 2–5 秒輪詢 `GET /projects/{id}`，依 status 顯示「正在分析高光／可以開始編輯／正在渲染／影片已完成」。

## 給 P2 簡報用的「三步精簡版」
評審頁面建議只畫三格，細節收進附錄：

```mermaid
flowchart LR
    S1["① 上傳直播素材<br/>指定短片秒數"] --> S2["② AI 分析 + 自動組片<br/>高光候選 + 初始草稿"] --> S3["③ 微調後一鍵渲染<br/>下載成品短片"]
```
