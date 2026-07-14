# ROADMAP — frontend-web（編輯器控制面・前端）

> 權威願景：[`../docs/demand.md`](../docs/demand.md)。本檔只列**目標**與**原則**，不含實作細節；逐步推進，最後與 backend-api／infra 整合。

## 目標（里程碑）
- **M1 專案與上傳**：登入（Cognito）→ 建立 Project（指定目標秒數 ≤ 60s）→ 取得 presigned → 瀏覽器直傳原始影片至 S3（multipart）→ 顯示「分析中」狀態。
- **M2 編輯器草稿**：四區編輯器 UI（Video Preview／Highlight Candidates／Project Settings〔target・subtitle・effect・aspect〕／Timeline），呈現分析回來的高光候選與初始 Timeline 草稿。
- **M3 Timeline 互動**：拖曳排序、刪除、鎖定、修改起訖、重新自動組片；每次變更以**新版本**送出 timeline。
- **M4 Render 與交付**：提交 Render → 依 Project／Render 狀態顯示進度 → 完成後預覽播放 ＋ Signed URL 下載 Artifact。

## 原則（前端專屬）
- 靜態匯出，S3 + CloudFront 交付；與後端所有互動**只走 `contracts/openapi.yaml`** 定義的 endpoint。
- UI 由**狀態機驅動**（Project／Render 狀態 → 畫面文案與可用操作）；時間一律 **ms**；aspect 支援 16:9／9:16／1:1。
- 後端未就緒時以 `contracts/` 的 sample 當 mock，介面不得偏離契約。
- 前端不持有商業邏輯真相（真相在契約與後端）；除了用後端給的 presigned URL 直傳／下載，不直接呼叫 AWS SDK。

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
