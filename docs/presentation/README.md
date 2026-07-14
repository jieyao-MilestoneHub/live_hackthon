# 簡報交付物（Presentation Deliverables）

> Epic #3。評審交付物工作流（label: `presentation`）。本目錄集中管理 P1–P7 的**可維護源文件**；投影片視覺版另以 HTML deck 產出。

## 交付物索引

| # | 交付物 | 文件 | 狀態 | 依賴 |
|---|--------|------|------|------|
| P1 (#15) | 端對端使用者流程圖 | [`P1-user-flow.md`](./P1-user-flow.md) | ✅ 可用 | — |
| P2 (#16) | 簡報大綱與投影片 | [`P2-slides.md`](./P2-slides.md) | ✅ 大綱完成 | P1 / S1 架構 / P3 |
| P3 (#17) | 成效衡量指標 | [`P3-metrics.md`](./P3-metrics.md) | 🟡 定義完成，數字待 #14 實測 | #14 (S7) |
| P4 (#18) | 成果截圖與 Demo 素材 | [`P6-demo-script.md`](./P6-demo-script.md#p4-成果截圖採集清單) | 🟡 採集清單就緒，待實跑 | #14 (S7) |
| P5 (#19) | 測試紀錄文件 | [`P5-test-record.md`](./P5-test-record.md) | ✅ 離線測試已記錄（103 passed） | — |
| P6 (#20) | Demo 操作腳本 | [`P6-demo-script.md`](./P6-demo-script.md) | ✅ 腳本 + 話術 + 備援 | P7 |
| P7 (#21) | 備援 Demo 錄影 | [`P6-demo-script.md`](./P6-demo-script.md#p7-備援錄影分鏡) | 🟡 分鏡就緒，待錄製 | #14 (S7) |

## 誠實標記原則
- **✅ 可用**：內容完整、可直接用於簡報。
- **🟡 待實測/待錄製**：結構與方法就緒，凡是需要「真實 E2E 跑一次」才能填的數字（成效指標、成果截圖、成功錄影）都留明確占位 `〈待 #14 填〉`，**不編造**。
- 一旦 #14 (S7) 端到端整合測試在真實 AWS 跑通，回填 P3 數字、P4 截圖、P7 錄影即可結案。

## 依賴的既有資料
- 架構圖（S1 / #8）：見 `docs/aws-infra.md` 與已產出的「AWS 架構現況圖」互動頁。
- 權威願景：`docs/demand.md`（兩段式影片編輯器）。
- 部署現況：專案根 `CLAUDE.md`「目前部署（dev）」。
