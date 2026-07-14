# 浪 LIVE — AI 直播高光剪輯

從直播/影片中自動辨識高光時刻，產出可分享的精華短片。近即時非同步 pipeline，設計見 [`docs/aws-infra.md`](docs/aws-infra.md)，總體規劃見 [`ROADMAP.md`](ROADMAP.md)。

## 技術棧

| 層 | 技術 | 部署 |
|---|---|---|
| 前端 `frontend-web/` | Next.js（靜態匯出） | S3 + CloudFront |
| 後端 `backend-api/` | FastAPI（含分析模組） | ECR → App Runner |
| Infra `infra/` | Terraform | ap-northeast-1（東京） |

## Repo 佈局

```
frontend-web/   Next.js 上傳/狀態/結果頁
backend-api/    FastAPI Job API + analysis 模組（transcript.v1 → highlights.v1）
infra/          Terraform（前端 CDN、後端 App Runner、foundation）
contracts/      跨組介面真相來源（schemas + openapi + samples）
docs/           架構設計（aws-infra.md）
```

## 介面契約（跨組請勿任意變更；變更需三組同步）

- `contracts/transcript.v1.schema.json` — 逐字稿正規化格式（issue #4）
- `contracts/highlights.v1.schema.json` — 高光輸出格式（issue #6，分析↔系統介面）
- `contracts/openapi.yaml` — Job API（前後端共用）

## 平行開發（git worktree）

每位成員在自己的 worktree 開發，只改自己的目錄，合併回 `main` 幾乎無衝突：

```bash
# 於 repo 根目錄一次建好三個 worktree（若尚未建立）
git worktree add ../live_hackthon-frontend-web feat/frontend-web
git worktree add ../live_hackthon-backend-api  feat/backend-api
git worktree add ../live_hackthon-infra         feat/infra

git worktree list      # 查看
```

Commit 訊息請引用對應 issue，例如 `feat(analysis): rule-based highlight scoring (#5)`。

## 各區快速上手

### backend-api
```bash
cd backend-api
python3 -m venv .venv && source .venv/Scripts/activate   # Windows Git Bash
python3 -m pip install -r requirements.txt
uvicorn app.main:app --reload --port 8080                # http://localhost:8080/health
python3 -m pytest                                        # 分析模組離線測試
```

### frontend-web
```bash
cd frontend-web
npm install
NEXT_PUBLIC_API_BASE_URL=http://localhost:8080 npm run dev   # http://localhost:3000
npm run build                                                # 產出 out/ 靜態站
```

### infra
```bash
cd infra/environments/dev
terraform init && terraform validate
# 部署需 AWS 憑證：aws configure && ../../deploy.sh
```

## 環境

- `dev`（本回合部署）→ `staging` / `prod` 續建。資源命名 `*-dev`。
