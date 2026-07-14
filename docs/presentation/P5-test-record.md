# P5 — 測試紀錄文件（#19）

> 驗收：測試案例、輸入/輸出、結果整理成文件。
> 本文件記錄**已實際執行**的離線測試（moto 模擬 AWS，`USE_INMEMORY=0` 走真實 code path）。真實 AWS 端到端（#14 S7）跑通後另補一節。

## 一、離線測試套件結果（實測）

- 指令：`cd backend-api && python -m pytest -q`
- 環境：Python 3.11、moto（模擬 DynamoDB/S3）、`USE_INMEMORY=0`
- **結果：103 passed**（執行時間約 90 秒）
- 量測日：2026-07-14（審查當下實跑；註：後端工作樹當時正被並發開發，數字為當下快照）

### 測試分佈

| 測試檔 | 案例數 | 覆蓋範圍 |
|--------|-------:|----------|
| `test_api.py` | 18 | 10 個 Editor API 端點的 HTTP 全鏈路（經 moto 打真 DynamoDB/S3 路徑） |
| `test_highlights.py` | 10 | 規則式高光偵測（關鍵詞/驚嘆/疊字/語速加權、門檻、合併、padding、top-N） |
| `test_composer.py` | 9 | Timeline 組片（貪婪填秒數、≤60s、鎖定/排除、依來源時間排序、契約自驗） |
| `test_attribution_fusion.py` | 8 | 具名說話者融合（Transcribe + 人物 + ASD + Nova 複核） |
| `test_adapters_stub.py` | 7 | AWS adapter 的 Stub 實作（Transcribe/Rekognition/Bedrock） |
| `test_workers.py` | 6 | analysis/composer worker 純函式 I/O |
| `test_asd_worker.py` | 5 | Active Speaker Detection 啟發式 |
| `test_creative.py` | 4 | 字幕/特效/render_spec 計畫產生 |
| `test_attribution_api.py` | 4 | 具名逐字稿 API |
| `test_render_worker.py` | 3 | 渲染狀態機 + manifest（artifact.v1） |
| `test_contracts.py` | 3 | 契約 schema 驗證 |
| `test_attribution_pipeline.py` | 3 | 具名逐字稿編排 |
| `test_attribution_contracts.py` | 3 | 具名逐字稿契約 |
| `test_attribution_persistence.py` | 2 | 具名逐字稿持久化 |
| `test_attribution_mount.py` | 2 | router 掛載 |
| **合計** | **103** | |

## 二、代表性測試案例（輸入 / 輸出 / 結果）

### 案例 1：建立 Project 並取得上傳授權
- **輸入**：`POST /projects {title, target_duration_ms: 30000}` → `POST /projects/{id}/upload-session`
- **預期輸出**：Project item（status `CREATED`）寫入；回傳 presigned multipart URLs。
- **結果**：✅ `test_api.py`（狀態轉移守衛 + 真 S3 multipart presign）。

### 案例 2：高光偵測（規則式）
- **輸入**：`transcript.v1.json`（含關鍵詞/驚嘆句）
- **預期輸出**：`highlights.v1.json`，各 highlight 有 `start_ms/end_ms/score/reason/suggested_title`，score 正規化、相鄰熱段合併、取 top-N。
- **結果**：✅ `test_highlights.py`（10 案例）。

### 案例 3：Composer 組出 ≤60s Timeline
- **輸入**：多個 highlights + `target_duration_ms=30000` + 鎖定/排除設定
- **預期輸出**：`timeline.v1.json`，`actual_duration_ms` 誤差 ±0.5s、依來源時間排序、不超過 60s。
- **結果**：✅ `test_composer.py`（9 案例）。

### 案例 4：渲染成品與 manifest
- **輸入**：凍結的 timeline_version → 渲染
- **預期輸出**：狀態機推進到 `SUCCEEDED`、Project → `ARTIFACT_READY`、產出 `manifest.json`（artifact.v1，`validate_artifact` 通過）。
- **結果**：✅ `test_render_worker.py`（encode 為 stub bytes，真 FFmpeg 由 `RENDER_ENCODER=ffmpeg` 切換）。

## 三、部署煙霧測試（M0/M1 已上線驗證）
- 後端 `GET /health` → **200**（API Gateway → Lambda）。
- 前端 CloudFront 首頁 → **200**。
- 來源：`CLAUDE.md`「目前部署」＋交接紀錄（M0 端到端建 job → SUCCEEDED → 回傳 highlights 已驗證）。

## 四、待補（#14 S7 真實 AWS 端到端）
- [ ] 上傳真影片 → EventBridge → Step Functions 全綠一次。
- [ ] Transcribe 真逐字稿（秒轉 ms 正確）。
- [ ] Batch + FFmpeg 產出真 `final.mp4` 可播放。
- [ ] 失敗情境：壞檔、超長、Transcribe 失敗 → `FAILED` 狀態與 DLQ 行為。
- [ ] 併發/壓測：多 Project 同時分析。
