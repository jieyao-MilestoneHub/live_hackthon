# ROADMAP — backend-api（編輯器控制面・API ＋ 分析處理面・workers）

> 權威願景：[`../docs/demand.md`](../docs/demand.md)。本檔只列**目標**與**原則**，不含實作細節；逐步推進，最後與 frontend-web／infra 整合。

## 目標（里程碑）
- **M1 契約與 Project API（本輪契約管家）**：把 `contracts/` 升級為 demand.md 的 Project／毫秒版（`transcript.v1`、`highlights.v1`、`timeline.v1`、`subtitle.v1`、`effects.v1`、`render_spec.v1`、`artifact.v1`、`openapi.yaml`）並推 `main`；實作 Editor API（`/projects`、`/projects/{id}/upload-session`、`GET /projects/{id}`、`/highlights`、`GET/PUT /timeline`、`/compose`、`/renders`、`GET /renders/{id}`、`/artifacts/{id}/download`），驗 Cognito JWT，狀態存 DynamoDB `VideoEditor`，presigned upload／download。
- **M2 分析與組片 worker**：Analysis Worker（`transcript.v1` ＋ metadata ＋ target → `highlights.v1`）；Composer Worker（`highlights.v1` ＋ target ＋ locked/excluded → `timeline.v1`，做秒數最佳化、不碰 FFmpeg）。
- **M3 創意計畫 worker**：Creative Planning Worker（`timeline.v1` → `subtitle.v1` ＋ `effects.v1`〔含 `effect_seed`〕＋ `render_spec`）。
- **M4 渲染 worker**：FFmpeg Render Worker 容器（source ＋ timeline ＋ subtitle ＋ effect_plan → `final.mp4`／`preview.mp4`／`thumbnail.jpg`），**一次**完成剪接／串接／轉場／字幕／特效／音訊／編碼。

## 原則（後端專屬）
- 本輪**契約管家**：契約先行、對齊 demand.md、變更即推 `main` 並通知另外兩組。
- Worker 皆為**純函式**：輸入／輸出是 S3 上的**版本化 JSON** ＋ DynamoDB 狀態；冪等鍵 `bucket+key+version_id`。
- 控制面（HTTP API）不跑 FFmpeg；長活交非同步 worker。Timeline **只新增版本不覆蓋**；Render 用固定 `effect_seed` 確保可重現。
- **不要三次編碼**：Composer 只出 Timeline／EDL、Creative 只出計畫、FFmpeg 一次出成品。

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
