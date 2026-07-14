# contracts/ — 浪 LIVE 跨組單一真相來源

本目錄是 **analysis / system(backend) / presentation 三組共用的契約單一真相來源**。
前後端與 worker 之間所有資料交換一律以此處的 JSON Schema / OpenAPI 為準。

> **M1 升級公告（Project / 毫秒版）**
> 契約已從 **M0（job / 秒）** 升級為 **M1（Project / 毫秒）**：核心實體改為 **Project**（不再是 job）、
> 時間單位一律 **毫秒（ms，integer）**、固定 id 命名 `project_id / highlight_id / timeline_version /
> render_id / artifact_id / tenant_id`。權威來源為 `docs/demand.md`（§四 API、§十六 S3、§十七 DynamoDB
> `VideoEditor`、§十八 狀態機）。對應 issue #4（transcript）、#6（highlights）。

## Schema 清單

| 檔案 | 用途 | 生產者 → 消費者 |
|---|---|---|
| `chatlog.v1.schema.json` | 正規化聊天室 log（**聊天優先分析輸入**） | 分析 pipeline（Clean）→ Chat Analysis Worker |
| `transcript.v1.schema.json` | 正規化逐字稿 | 分析 pipeline（Normalize）→ Analysis Worker |
| `highlights.v1.schema.json` | 高光候選（含聊天優先 additive 欄位） | Analysis Worker → Composer / 編輯器 |
| `annotations.v1.schema.json` | 結構化標註（5 維度 + 敘事節拍，以 `highlight_id` 關聯） | 標註產生器（規則+AI+人工）→ 編輯器 |
| `timeline.v1.schema.json` | 剪輯決策表 / EDL（append-only 版本化） | Composer / 編輯器 → Render |
| `subtitle.v1.schema.json` | 動態字幕計畫 | Creative Worker → FFmpeg |
| `effects.v1.schema.json` | 特效計畫（含 `effect_seed`） | Creative Worker → FFmpeg |
| `render_spec.v1.schema.json` | 渲染規格書（彙整輸入/輸出） | Render Workflow → FFmpeg Worker |
| `artifact.v1.schema.json` | 最終產物清單 / manifest.json | Render Workflow → 下載 API |
| `openapi.yaml` | 編輯器控制面 REST API（前後端共用） | frontend ↔ backend |

`samples/` 內每個 schema 各有一份有效實例（皆以 `project-123` 串成同一條敘事），可作為
契約驗證與 mock 資料。

> **⚠️ 時間單位範疇（epoch vs 影片相對）**：`chatlog.v1` 的 `time_ms` / `*_epoch_ms` 是
> **牆鐘 Unix epoch 毫秒（UTC）**，其餘所有契約的 `*_ms` 都是**影片相對毫秒（0 = 影片起點）**。
> 兩者唯一橋樑是 `Project.video_start_epoch_ms`（來自 MP4 OBS `creation_time`）：
> `video_relative_ms = clamp(chat_epoch_ms − video_start_epoch_ms, 0, source_duration_ms)`。
> `chatlog.v1` 以 `time_base: "epoch_ms"` 明示，避免與影片相對毫秒混用。聊天觀眾反應落後
> （chat lag）的事件校正是疊在換算之上、每個 highlight 的 `correction.offset_ms`。

## 命名 / 版本慣例

- 檔名：`<name>.v<MAJOR>.schema.json`（如 `timeline.v1.schema.json`）。
- 每份實例必帶 `schema_version`（schema 內以 `"const"` 鎖定，如 `"const": "timeline.v1"`）。
- `$id`：`https://live-hackthon/contracts/<name>.v1.schema.json`。
- JSON Schema **Draft 2020-12**、`additionalProperties: false`、內部參照用 `#/$defs/...`（不跨檔 `$ref`）。
- 破壞性變更 → 升 MAJOR（`.v2`）並保留舊檔，讓消費者可漸進遷移。
- OpenAPI 以 `info.version` 版本化（目前 `0.2.0`），與 schema 的 `vN` 分開計。

## M0 → M1 遷移對照

| M0（job / 秒） | M1（Project / 毫秒） |
|---|---|
| `job_id` | `project_id` |
| `clip_id`（highlights） | `highlight_id` |
| `*_sec`（number，秒） | `*_ms`（integer，毫秒） |
| `duration_sec` | `duration_ms` / `source_duration_ms` |
| `title`（highlights item） | `suggested_title` |
| （無） | 新增 `transcript`、`source_duration_ms`、`selected`、`locked`（highlights） |
| Job API `/jobs`（`openapi.yaml`） | Project API `/projects`、`/renders`、`/artifacts` |
| DynamoDB `VideoJobs` | DynamoDB `VideoEditor`（單表，PK=`PROJECT#{id}`） |

## 治理規則（三組共同遵守）

1. `contracts/` 為單一真相來源。**任何欄位/語意變更 = 先改 `contracts/` → 升 `schema_version` →
   推 `main` → 通知另外兩組**。任一 worktree 不得私自分歧。
2. 各 worktree 動工前 `git fetch && git merge origin/main` 取得最新契約。
3. 下游鏡像（如 `frontend-web/types.ts`、`backend-api/app/schemas.py`）須隨契約同步更新。

## 權威來源位置

`docs/demand.md`（Project/毫秒版的完整需求）目前**只存在於 main checkout 的工作目錄、尚未納入 git
追蹤**：`C:\Users\USER\Desktop\Develop\live_hackthon\docs\demand.md`。建議由 repo 維護者把它 commit 進
`main`，讓三組 worktree 都能取得同一份權威需求。
