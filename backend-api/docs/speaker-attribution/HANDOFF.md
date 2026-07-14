# Speaker-Attributed Transcript — 整合交接（backend-api）

具名說話者逐字稿功能。把 Transcribe 匿名 `spk_N` 融合 Rekognition 人物 + Active Speaker
Detection（+ Nova 語意複核）→ 每句帶「時間 / 群組 / 具名人物 / 角色 / 方法 / 可信度」的
`attributed_transcript.v1`，供編輯器顯示「主播 A：…」與人工更正。

> **不衝突原則**：本功能**全放新檔/新目錄**，未編輯任何被其他 session 佔用的共用檔
> （`main.py / repository.py / settings.py / storage.py / schemas.py / conftest.py`）。
> 只剩下面兩個「一行掛載」交接動作，待共用檔穩定後由維護者加上。

## 1. 掛載 API router（main.py 加兩行）

```python
# app/main.py
from app.attribution_api import router as attribution_router
app.include_router(attribution_router)
```

新端點（皆為新路由，不影響既有 `/projects` 端點）：

| Method | Path | 用途 |
|---|---|---|
| POST | `/projects/{id}/people` | 註冊主角（方案A：參考照片 → Rekognition Collection） |
| GET | `/projects/{id}/people` | 人物名冊 |
| POST | `/projects/{id}/attribution` | 跑 pipeline 產生具名逐字稿並落地 |
| GET | `/projects/{id}/transcript` | 讀取 `attributed_transcript.v1` |
| PATCH | `/projects/{id}/speakers/{cluster_id}` | 把整個群組標成某人物（傳播到所有句） |
| PATCH | `/projects/{id}/utterances/{utterance_id}` | 單句更正 |

## 2. 環境變數

見 `backend-api/.env.attribution.example`（`app/aws/config.py` 讀取）。離線預設
`USE_INMEMORY=1` 走 stub adapter，無需 AWS。實接時設 `USE_INMEMORY=0` + 下列 infra。

## 3. 檔案地圖（皆新增）

```
contracts/
  people.v1.schema.json  attributed_transcript.v1.schema.json  asd_result.v1.schema.json
  samples/{people,attributed_transcript,asd_result}.sample.json
  openapi.attribution.yaml            # 附加 OpenAPI paths（待併入 openapi.yaml，info.version→0.3.0）
backend-api/
  analysis/attribution/{__init__,ports,scoring,fusion,pipeline}.py   # 純融合 + 編排
  app/aws/{__init__,ports,config,transcribe,rekognition,bedrock_nova,factory}.py  # AWS adapters
  app/attribution_repository.py       # 自帶持久化（PERSON#/SPEAKER# + work-bucket JSON）
  app/attribution_api.py              # APIRouter + Pydantic（掛載見上）
  workers/asd/{__init__,heuristic,worker}.py  Dockerfile.asd            # ASD worker
  tests/test_attribution_*.py  tests/test_asd_worker.py  tests/test_adapters_stub.py
  .env.attribution.example
docs/speaker-attribution/            # 本交接包
```

**加法式編輯**（僅這兩個共用檔，且與 M2 無重疊區段）：`analysis/validate.py`（+3 validator）、
`tests/test_contracts.py`（+3 schema/sample）。

## 4. 驗證（離線，全綠）

```bash
cd backend-api && python3 -m pytest -q          # 全部通過（含 attribution 相關 40+ 測試）
python3 -m pytest tests/test_attribution_fusion.py tests/test_attribution_pipeline.py \
                  tests/test_attribution_api.py tests/test_asd_worker.py -q
```

API 冒煙（掛載 router 後）：`POST /projects/p1/people` → `POST /projects/p1/attribution`
→ `GET /projects/p1/transcript` → `PATCH …/utterances/{id}`。

## 5. 跨組後續（不在此任務施作）

- **[infra]** 見 `infra-analysis-speakers.tf.example` + `attribute-speakers.asl.json`：新增
  IAM（Transcribe/Rekognition/Bedrock）、Rekognition 服務角色 + `AmazonRekognition*` SNS/SQS、
  Bedrock 跨區雙-ARN 政策、Transcribe EventBridge 規則；每個新服務先跑 SCP create→delete probe。
- **[frontend]** 見 `frontend-handoff.md`：新增「具名逐字稿」面板 + `types.ts`/`api.ts`/`mock.ts`。
- **[contracts/main]** 把三份新 schema + `openapi.attribution.yaml` 併入 `main`（現仍 M0），
  依治理規則通知三組；`info.version` 0.2.0→0.3.0（加法端點，非破壞）。

## 6. 已知限制

- ASD 為啟發式代理（`workers/asd/heuristic.py`）；真模型（TalkNet/Light-ASD on Batch/SageMaker）
  以 `Dockerfile.asd` 為接縫換入，fusion 不需改。
- 跨影片聲紋辨識未做；MVP 僅單影片內 diarization + 至少一段高信心臉部/嘴型對齊。
- Nova **不看音訊、不命名人物** → 僅對 `needs_review` 片段填語意建議，不覆蓋高信心結果。
- Transcribe 輸出秒→adapter 轉 ms；Rekognition `Timestamp` 已是 ms。
