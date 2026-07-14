# 浪 LIVE — frontend-web

AI 直播高光剪輯的前端走路骨架（walking skeleton）。
Next.js 14（App Router、TypeScript），設定為 **靜態匯出**（`output: 'export'`），
部署目標為 **S3 + CloudFront**。透過 `NEXT_PUBLIC_API_BASE_URL` 呼叫 FastAPI 後端。

## 頁面

- `/`（`app/page.tsx`）— 上傳頁：選擇影片檔案，呼叫 `POST /jobs`，
  然後導向狀態頁。走路骨架階段只建立 job 並導向，真正的 S3 multipart
  上傳為團隊後續工作（程式碼內有 `TODO(team, #16)`）。
- `/jobs?id=...`（`app/jobs/page.tsx`）— 狀態＋結果頁：以 **query string**
  `?id=` 讀取 job id（client component），輪詢 `GET /jobs/{id}`，顯示
  status / stage / progress；`SUCCEEDED` 時列出高光短片（clip_id、title、
  起訖秒數、score、reason）與下載按鈕。

> 注意：刻意使用 query-param 路由（`/jobs?id=`）而非動態 `[id]` 路由——
> 動態路由在 `output: 'export'` 下需要 `generateStaticParams`，否則無法匯出。

## API 契約

型別鏡射自 `../contracts/openapi.yaml`（`types.ts`）。
四個端點的 typed client 在 `lib/api.ts`：`createJob`、`getJob`、`getDownloadUrl`
（以及 base URL）。當後端無法連線時，會自動退回 `lib/mock.ts` 的本地 mock
（形狀取自 `../contracts/samples/highlights.sample.json`），讓 UI 在 dev 也能渲染。

## 環境變數

複製 `.env.example` 為 `.env.local` 並視需要調整：

```
NEXT_PUBLIC_API_BASE_URL=http://localhost:8080
```

`NEXT_PUBLIC_*` 變數在 **build 時** 內嵌，故切換後端位址需重新 build。

## 開發與建置

```bash
npm install       # 安裝相依套件
npm run dev       # 本機開發（http://localhost:3000）
npm run build     # 靜態匯出 → 產生 out/（含 out/index.html）
```

建置成功後，`out/` 內為純靜態檔案，可直接上傳 S3 / 由 CloudFront 提供。
本地預覽匯出結果可用任一靜態伺服器，例如：

```bash
npx serve out
```

## 靜態匯出限制（重要）

- 無 SSR、無 Server Components 於 runtime、無 API routes——全部為 client-side
  render + fetch。
- 圖片最佳化關閉（`images.unoptimized: true`）。
- 使用 `useSearchParams` 的元件需包在 `<Suspense>` 內（狀態頁已處理）。
- `output: 'export'` 下 `npm run start`（`next start`）不適用；請改用靜態
  伺服器提供 `out/`（見上）。
