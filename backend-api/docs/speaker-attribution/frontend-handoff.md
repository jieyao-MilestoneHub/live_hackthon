# Frontend 交接 — 具名逐字稿面板（feat/frontend-web）

在既有四區編輯器加**第五區「逐字稿」**：逐句渲染「主播 A：…」，`needs_review`/`unknown`
醒目標示並提供更正下拉。全部走既有 `lib/api.ts` + `lib/mock.ts` graceful fallback 樣式。

## 1. `types.ts` 新增介面（鏡像 `attributed_transcript.v1`）

```ts
export type AttributionStatus =
  | "confirmed" | "needs_review" | "unknown" | "overlapping_speech" | "off_screen";
export type PersonRole = "protagonist" | "host" | "guest" | "unknown";

export interface Participant {
  person_id: string;
  display_name: string;
  role: PersonRole;
  identity_source: "rekognition_collection" | "user_label" | "inferred";
}

export interface Attribution {
  status: AttributionStatus;
  method: string;
  confidence: number;
  face_similarity?: number | null;
  lip_sync_confidence?: number | null;
  visible_ratio?: number | null;
}

export interface Utterance {
  utterance_id: string;
  start_ms: number;
  end_ms: number;
  text: string;
  speaker_cluster_id: string;         // spk_0…
  person_id: string | null;
  display_name: string | null;
  role: PersonRole | null;
  attribution: Attribution;
  corrected_by?: string | null;
  corrected_at?: string | null;
}

export interface AttributedTranscript {
  schema_version: "attributed_transcript.v1";
  project_id: string;
  language_code: string;
  participants: Participant[];
  utterances: Utterance[];
}
```

## 2. `lib/api.ts` 新增

```ts
export const getPeople = (id: string) =>
  fetchJson<Participant[]>(`/projects/${id}/people`);

export const runAttribution = (id: string, use_asd = true) =>
  fetchJson<AttributedTranscript>(`/projects/${id}/attribution`, {
    method: "POST", body: JSON.stringify({ use_asd }),
  });

export const getTranscript = (id: string) =>
  fetchJson<AttributedTranscript>(`/projects/${id}/transcript`); // 404 → 尚未產生

export const patchSpeaker = (id: string, cluster: string, person_id: string) =>
  fetchJson(`/projects/${id}/speakers/${cluster}`, {
    method: "PATCH", body: JSON.stringify({ person_id }),
  });

export const patchUtterance = (id: string, uttId: string, person_id: string | null) =>
  fetchJson<Utterance>(`/projects/${id}/utterances/${uttId}`, {
    method: "PATCH", body: JSON.stringify({ person_id }),
  });
```

`lib/mock.ts`：加 `MOCK_TRANSCRIPT`（照 `contracts/samples/attributed_transcript.sample.json`），
讓面板無後端也能渲染（沿用現有 offline fallback）。

## 3. 面板 UX 要點

- 逐句：`[mm:ss.mmm] {display_name ?? "未知說話者"}：{text}`。
- 狀態徽章：`confirmed`(綠) / `needs_review`(黃) / `unknown`(灰) / `overlapping_speech`(橘) / `off_screen`(藍)。
- 更正：句旁下拉列 `participants` + 「未知」；選定 → `patchUtterance`；群組層級可 `patchSpeaker`。
- 顯示證據 tooltip：`face_similarity / lip_sync_confidence / confidence`。
- 讀取流程：`getTranscript` 404 → 顯示「產生具名逐字稿」按鈕 → `runAttribution` → 重取。
