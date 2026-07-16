// Typed client for the 浪 LIVE Editor API (contracts/openapi.yaml v0.8.0).
// Base URL from NEXT_PUBLIC_API_BASE_URL (baked at build time).
//
// Mock fallback is DEV-ONLY: it kicks in only when the base URL points at
// localhost (ALLOW_MOCK) AND the failure is a network/offline/501 condition
// (isOfflineError). Against a real backend (QA/prod build), every real HTTP
// error — 401/403/404/409/500 — is re-thrown so callers surface it instead of
// silently showing fabricated data. A 401 also clears auth so the login gate
// re-prompts (mid-session token expiry). See lib/auth.ts for the token.
//
// Swapping in the real backend later needs no page changes: this file + types.ts
// are the only contract-facing surface. When a Cognito IdToken is present
// (lib/auth.ts) it is attached as `Authorization: Bearer <token>` on every call.

import type {
  AnalyzeRequest,
  AnalyzeResult,
  Artifact,
  ChatUploadUrl,
  ComposeRequest,
  DownloadUrl,
  HighlightList,
  ModerationView,
  ProgressView,
  Project,
  ProjectCreate,
  ProjectCreated,
  Render,
  RenderCreated,
  Route,
  Timeline,
  TimelineVersionResponse,
  UploadCompleted,
  UploadPartETag,
  UploadSession,
  UploadSessionCreate,
  VideoTimebaseRequest,
} from '@/types';
import { getIdToken, logout } from './auth';
import {
  markMockAnalysisStart,
  mockCreateRender,
  mockDownloadUrl,
  mockListArtifacts,
  mockHighlightList,
  mockProject,
  mockProjectStatusFor,
  mockRender,
  mockTimeline,
  mockUploadSession,
} from './mock';

export const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8080'
).replace(/\/$/, '');

/**
 * Whether the offline mock fallback is allowed. Only when the app targets a
 * local dev backend (localhost). A QA/prod build points at the real API Gateway,
 * so ALLOW_MOCK is false and every backend error surfaces to the caller instead
 * of being masked by fabricated data. Guards every catch below alongside
 * isOfflineError().
 */
export const ALLOW_MOCK = /localhost|127\.0\.0\.1/.test(API_BASE_URL);

/** Prefix for projects synthesized client-side when the backend is offline. */
const MOCK_PROJECT_PREFIX = 'mock_';

export function isMockProjectId(projectId: string): boolean {
  return projectId.startsWith(MOCK_PROJECT_PREFIX);
}

/**
 * Error carrying the HTTP status so callers can tell a real client error (413
 * too large, 415 bad type, 404) from an offline/unreachable backend (status 0).
 * The mock-fallback code paths only kick in for status 0 / 5xx, never for a 4xx
 * the server deliberately returned — otherwise a rejected upload would look OK.
 */
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

/** True when the failure is a network/offline condition or a not-yet-built (501) endpoint — safe to fall back to mock data. */
function isOfflineError(err: unknown): boolean {
  if (err instanceof ApiError) return err.status === 0 || err.status === 501 || err.status >= 502;
  return true; // non-ApiError (unexpected) → treat as offline for the dev mock flow
}

/** Base JSON headers plus `Authorization: Bearer <IdToken>` when logged in. */
function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  const token = getIdToken();
  if (token) headers['Authorization'] = `Bearer ${token}`;
  return headers;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: { ...authHeaders(), ...((init?.headers as Record<string, string>) ?? {}) },
    });
  } catch (err) {
    // Network failure / backend unreachable — status 0 signals "offline".
    throw new ApiError(`API ${init?.method ?? 'GET'} ${path} network error: ${err}`, 0);
  }
  if (!res.ok) {
    // 401 = missing/expired/invalid token at the API Gateway JWT authorizer.
    // Clear auth so AuthGate re-prompts login instead of the app limping on with
    // an expired token (Cognito IdTokens expire ~1h).
    if (res.status === 401) logout();
    throw new ApiError(`API ${init?.method ?? 'GET'} ${path} failed: ${res.status}`, res.status);
  }
  return (await res.json()) as T;
}

/** POST /projects — create a project. Falls back to a synthetic mock when offline. */
export async function createProject(body: ProjectCreate): Promise<ProjectCreated> {
  try {
    return await request<ProjectCreated>('/projects', {
      method: 'POST',
      body: JSON.stringify(body),
    });
  } catch (err) {
    if (!ALLOW_MOCK || !isOfflineError(err)) throw err;
    console.warn('[api] createProject fell back to mock (backend unreachable):', err);
    const projectId = `${MOCK_PROJECT_PREFIX}${Date.now()}`;
    return {
      project_id: projectId,
      status: 'CREATED',
      target_duration_ms: body.target_duration_ms,
      source_key: `tenant=demo/project=${projectId}/source/source.mp4`,
    };
  }
}

/** POST /projects/{id}/upload-session — presigned multipart authorization. */
export async function createUploadSession(
  projectId: string,
  body: UploadSessionCreate,
): Promise<UploadSession> {
  // Mock projects (created while offline) never have a real S3 session.
  if (isMockProjectId(projectId)) {
    markMockAnalysisStart(projectId);
    return mockUploadSession(`tenant=demo/project=${projectId}/source/${body.filename}`);
  }
  try {
    return await request<UploadSession>(
      `/projects/${encodeURIComponent(projectId)}/upload-session`,
      { method: 'POST', body: JSON.stringify(body) },
    );
  } catch (err) {
    // Real client errors (413 too large, 415 bad type, 404) MUST surface so the
    // batch UI shows a per-file failure — do not mask them with an offline mock.
    if (!isOfflineError(err)) throw err;
    console.warn('[api] createUploadSession fell back to mock (backend unreachable):', err);
    // Offline: stamp analysis start so getProject walks the state machine.
    markMockAnalysisStart(projectId);
    return mockUploadSession(`tenant=demo/project=${projectId}/source/${body.filename}`);
  }
}

/** PUT one presigned part; resolves with its ETag (S3 returns it in a header). */
function putPart(
  url: string,
  blob: Blob,
  onLoaded: (loaded: number) => void,
  signal?: AbortSignal,
): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException('aborted', 'AbortError'));
      return;
    }
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', url);
    const onAbort = () => xhr.abort();
    signal?.addEventListener('abort', onAbort, { once: true });
    const cleanup = () => signal?.removeEventListener('abort', onAbort);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onLoaded(e.loaded);
    };
    xhr.onload = () => {
      cleanup();
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(xhr.getResponseHeader('ETag') ?? '');
      } else {
        reject(new Error(`part PUT failed: ${xhr.status}`));
      }
    };
    xhr.onerror = () => {
      cleanup();
      reject(new Error('part PUT network error'));
    };
    xhr.onabort = () => {
      cleanup();
      reject(new DOMException('aborted', 'AbortError'));
    };
    xhr.send(blob);
  });
}

/**
 * PUT a part with bounded retries. S3 `upload_part` is idempotent (re-PUTting
 * the same part number overwrites), so retrying is safe. Exponential backoff
 * (1s·2ⁿ, capped 20s) + jitter. Does not retry on abort.
 */
async function putPartWithRetry(
  url: string,
  blob: Blob,
  onLoaded: (loaded: number) => void,
  signal?: AbortSignal,
  maxAttempts = 4,
): Promise<string> {
  let lastErr: unknown;
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    if (signal?.aborted) throw new DOMException('aborted', 'AbortError');
    try {
      return await putPart(url, blob, onLoaded, signal);
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') throw err;
      lastErr = err;
      if (attempt < maxAttempts - 1) {
        onLoaded(0); // reset this part's counted progress before the retry
        const backoff = Math.min(20000, 1000 * 2 ** attempt) + Math.random() * 250;
        await new Promise((r) => setTimeout(r, backoff));
      }
    }
  }
  throw lastErr;
}

function simulateUpload(onProgress?: (pct: number) => void): Promise<void> {
  return new Promise<void>((resolve) => {
    let pct = 0;
    const tick = () => {
      pct += 20;
      onProgress?.(Math.min(100, pct));
      if (pct >= 100) resolve();
      else setTimeout(tick, 250);
    };
    tick();
  });
}

/**
 * POST /projects/{id}/upload-session/complete — the multipart-complete handshake.
 * Submits the ETags collected from each part PUT. This is what materializes
 * source.mp4 in the Raw bucket and triggers analysis; REQUIRED even for a
 * single-part upload. Falls back to the offline mock (stamps analysis start).
 */
export async function completeUploadSession(
  projectId: string,
  uploadId: string,
  parts: UploadPartETag[],
): Promise<UploadCompleted> {
  try {
    return await request<UploadCompleted>(
      `/projects/${encodeURIComponent(projectId)}/upload-session/complete`,
      { method: 'POST', body: JSON.stringify({ upload_id: uploadId, parts }) },
    );
  } catch (err) {
    if (!ALLOW_MOCK || !isOfflineError(err)) throw err;
    console.warn('[api] completeUploadSession fell back to mock:', err);
    markMockAnalysisStart(projectId);
    return {
      project_id: projectId,
      status: 'ANALYZING',
      key: `tenant=demo/project=${projectId}/source/source.mp4`,
    };
  }
}

// --- Chat-LOG analysis (analysis_source="chat") --------------------------
// A chat-first project pairs the video with a chat-room LOG CSV. After the
// video upload, the browser: presigns + PUTs the CSV (chat-upload), links the
// video timebase (duration), then triggers POST /analyze → COMPOSING, and a
// compose → READY_TO_EDIT. The Starter skips auto-Transcribe for these projects.

/** POST /projects/{id}/chat-upload — presign a single-part PUT for chat.csv. */
export async function createChatUpload(projectId: string): Promise<ChatUploadUrl> {
  try {
    return await request<ChatUploadUrl>(
      `/projects/${encodeURIComponent(projectId)}/chat-upload`,
      { method: 'POST' },
    );
  } catch (err) {
    if (!ALLOW_MOCK || !isOfflineError(err)) throw err;
    console.warn('[api] createChatUpload fell back to mock:', err);
    return {
      bucket: 'mock',
      key: `tenant=demo/project=${projectId}/source/chat.csv`,
      url: 'https://mock.local/chat-put',
      expires_in_sec: 900,
    };
  }
}

/** PUT the chat LOG CSV to its presigned URL. No-op for mock/stub URLs. */
export async function uploadChatCsv(session: ChatUploadUrl, file: File): Promise<void> {
  if (/mock\.local|localhost|stub-upload/.test(session.url)) {
    // Offline mock / local stub backend: no real object store to PUT into.
    return;
  }
  const res = await fetch(session.url, {
    method: 'PUT',
    headers: { 'Content-Type': 'text/csv' },
    body: file,
  });
  if (!res.ok) throw new Error(`chat CSV PUT failed: ${res.status}`);
}

/** PUT /projects/{id}/video-timebase — link video duration (+ optional epoch). */
export async function setVideoTimebase(
  projectId: string,
  body: VideoTimebaseRequest,
): Promise<Project> {
  try {
    return await request<Project>(
      `/projects/${encodeURIComponent(projectId)}/video-timebase`,
      { method: 'PUT', body: JSON.stringify(body) },
    );
  } catch (err) {
    if (!ALLOW_MOCK || !isOfflineError(err)) throw err;
    console.warn('[api] setVideoTimebase fell back to mock:', err);
    return mockProject(projectId, 'UPLOADING');
  }
}

/** POST /projects/{id}/analyze — run chat-LOG analysis (→ COMPOSING). */
export async function analyzeProject(
  projectId: string,
  body?: AnalyzeRequest,
): Promise<AnalyzeResult> {
  try {
    return await request<AnalyzeResult>(
      `/projects/${encodeURIComponent(projectId)}/analyze`,
      { method: 'POST', body: JSON.stringify(body ?? {}) },
    );
  } catch (err) {
    if (!ALLOW_MOCK || !isOfflineError(err)) throw err;
    console.warn('[api] analyzeProject fell back to mock:', err);
    markMockAnalysisStart(projectId);
    return { project_id: projectId, status: 'COMPOSING', highlight_count: 0, analysis_version: 'mock' };
  }
}

/** Options for uploadToS3. Defaults suit a single file; the batch orchestrator
 * caps overall in-flight PUTs via fileConcurrency × partConcurrency (3 × 2 = 6). */
export interface UploadToS3Options {
  /** Parts PUT concurrently for THIS file. Default 2. */
  partConcurrency?: number;
  /** Abort in-flight and pending part PUTs (e.g. user cancels the batch). */
  signal?: AbortSignal;
}

/**
 * Browser direct upload to the S3 Raw bucket using the session's presigned parts,
 * followed by the multipart-complete handshake.
 *
 * Splits the file evenly across parts and PUTs up to `partConcurrency` at a time
 * (each with retry/backoff), aggregating progress from a per-part loaded map so
 * the percentage stays correct under parallelism. Collects the per-part ETags (S3
 * returns them in the `ETag` response header — usually double-quoted, which S3's
 * CompleteMultipartUpload expects, so we keep it verbatim and only trim), then
 * calls completeUploadSession to finalize the object and trigger analysis.
 */
export async function uploadToS3(
  projectId: string,
  session: UploadSession,
  file: File,
  onProgress?: (pct: number) => void,
  opts?: UploadToS3Options,
): Promise<void> {
  const isMock =
    session.upload_id.startsWith('mock_upload_') ||
    session.parts.some((p) => p.url.startsWith('https://mock.local'));
  if (isMock) {
    // Offline: createUploadSession already stamped the mock analysis start.
    await simulateUpload(onProgress);
    return;
  }

  const parts = [...session.parts].sort((a, b) => a.part_number - b.part_number);
  const partSize = Math.ceil(file.size / parts.length);
  const completed: UploadPartETag[] = new Array(parts.length);
  const loadedByPart = new Map<number, number>();
  const reportProgress = () => {
    let total = 0;
    for (const v of loadedByPart.values()) total += v;
    const pct = file.size > 0 ? (total / file.size) * 100 : 100;
    onProgress?.(Math.min(100, Math.round(pct)));
  };

  const partConcurrency = Math.max(1, opts?.partConcurrency ?? 2);
  let nextIdx = 0;
  const worker = async (): Promise<void> => {
    for (;;) {
      if (opts?.signal?.aborted) throw new DOMException('aborted', 'AbortError');
      const idx = nextIdx++;
      if (idx >= parts.length) return;
      const part = parts[idx];
      const start = (part.part_number - 1) * partSize;
      const blob = file.slice(start, Math.min(start + partSize, file.size));
      const etag = await putPartWithRetry(
        part.url,
        blob,
        (loaded) => {
          loadedByPart.set(part.part_number, loaded);
          reportProgress();
        },
        opts?.signal,
      );
      loadedByPart.set(part.part_number, blob.size); // pin to exact size on success
      reportProgress();
      completed[idx] = { part_number: part.part_number, etag: (etag ?? '').trim() };
    }
  };

  const workers = Array.from({ length: Math.min(partConcurrency, parts.length) }, () => worker());
  await Promise.all(workers);
  onProgress?.(100);

  // Finalize the multipart upload — materializes source.mp4 + triggers analysis.
  await completeUploadSession(projectId, session.upload_id, completed);
}

/** GET /projects/{id} — poll project status. Falls back to a mock when offline. */
export async function getProject(projectId: string): Promise<Project> {
  try {
    return await request<Project>(`/projects/${encodeURIComponent(projectId)}`);
  } catch (err) {
    if (!ALLOW_MOCK || !isOfflineError(err)) throw err;
    console.warn('[api] getProject fell back to mock (backend unreachable):', err);
    return mockProject(projectId, mockProjectStatusFor(projectId));
  }
}

/** GET /projects/{id}/moderation — moderation verdict + audit trail. */
export async function getModeration(projectId: string): Promise<ModerationView> {
  try {
    return await request<ModerationView>(
      `/projects/${encodeURIComponent(projectId)}/moderation`,
    );
  } catch (err) {
    if (!ALLOW_MOCK || !isOfflineError(err)) throw err;
    console.warn('[api] getModeration fell back to empty view:', err);
    return { project_id: projectId, status: 'PENDING', events: [] };
  }
}

/** GET /projects/{id}/progress — AI 統整的即時進度旁白 feed（oldest→newest）。 */
export async function getProgress(projectId: string): Promise<ProgressView> {
  try {
    return await request<ProgressView>(
      `/projects/${encodeURIComponent(projectId)}/progress`,
    );
  } catch (err) {
    if (!ALLOW_MOCK || !isOfflineError(err)) throw err;
    console.warn('[api] getProgress fell back to empty view:', err);
    return { project_id: projectId, events: [] };
  }
}

/** POST /projects/{id}/moderation/override — moderator review (needs moderator role). */
export async function overrideModeration(
  projectId: string,
  decision: 'ALLOW' | 'BLOCK',
  note?: string,
): Promise<ModerationView> {
  return request<ModerationView>(
    `/projects/${encodeURIComponent(projectId)}/moderation/override`,
    { method: 'POST', body: JSON.stringify({ decision, note }) },
  );
}

/** GET /projects/{id}/highlights — highlight candidates (501 until M2 → mock). */
export async function getHighlights(projectId: string): Promise<HighlightList> {
  try {
    return await request<HighlightList>(
      `/projects/${encodeURIComponent(projectId)}/highlights`,
    );
  } catch (err) {
    if (!ALLOW_MOCK || !isOfflineError(err)) throw err;
    console.warn('[api] getHighlights fell back to mock:', err);
    return mockHighlightList(projectId);
  }
}

/** GET /projects/{id}/timeline — current timeline (501 until M2 → mock). */
export async function getTimeline(projectId: string): Promise<Timeline> {
  try {
    return await request<Timeline>(
      `/projects/${encodeURIComponent(projectId)}/timeline`,
    );
  } catch (err) {
    if (!ALLOW_MOCK || !isOfflineError(err)) throw err;
    console.warn('[api] getTimeline fell back to mock:', err);
    return mockTimeline(projectId);
  }
}

/** PUT /projects/{id}/timeline — persist edits as a new timeline version. */
export async function updateTimeline(
  projectId: string,
  timeline: Timeline,
): Promise<TimelineVersionResponse> {
  try {
    return await request<TimelineVersionResponse>(
      `/projects/${encodeURIComponent(projectId)}/timeline`,
      { method: 'PUT', body: JSON.stringify(timeline) },
    );
  } catch (err) {
    if (!ALLOW_MOCK || !isOfflineError(err)) throw err;
    console.warn('[api] updateTimeline fell back to mock:', err);
    return { timeline_version: (timeline.version ?? 1) + 1 };
  }
}

/** POST /projects/{id}/compose — re-run the composer (202 → new timeline version). */
export async function composeTimeline(
  projectId: string,
  body: ComposeRequest,
): Promise<TimelineVersionResponse> {
  try {
    return await request<TimelineVersionResponse>(
      `/projects/${encodeURIComponent(projectId)}/compose`,
      { method: 'POST', body: JSON.stringify(body) },
    );
  } catch (err) {
    if (!ALLOW_MOCK || !isOfflineError(err)) throw err;
    console.warn('[api] composeTimeline fell back to mock:', err);
    return { timeline_version: 2 };
  }
}

/** POST /projects/{id}/renders — submit a render (202). Falls back to a mock render. */
export async function createRender(
  projectId: string,
  timelineVersion?: number,
  route?: Route,
): Promise<RenderCreated> {
  try {
    const body: Record<string, unknown> = {};
    if (timelineVersion != null) body.timeline_version = timelineVersion;
    if (route) body.route = route;
    return await request<RenderCreated>(
      `/projects/${encodeURIComponent(projectId)}/renders`,
      { method: 'POST', body: JSON.stringify(body) },
    );
  } catch (err) {
    if (!ALLOW_MOCK || !isOfflineError(err)) throw err;
    console.warn('[api] createRender fell back to mock:', err);
    return mockCreateRender(projectId);
  }
}

/** GET /projects/{id}/artifacts — all finished artifacts (one per route). Mock on offline. */
export async function listArtifacts(projectId: string): Promise<Artifact[]> {
  try {
    return await request<Artifact[]>(
      `/projects/${encodeURIComponent(projectId)}/artifacts`,
    );
  } catch (err) {
    if (!ALLOW_MOCK || !isOfflineError(err)) throw err;
    console.warn('[api] listArtifacts fell back to mock:', err);
    return mockListArtifacts(projectId);
  }
}

/** GET /renders/{render_id} — poll render progress. Falls back to a mock render. */
export async function getRender(renderId: string): Promise<Render> {
  try {
    return await request<Render>(`/renders/${encodeURIComponent(renderId)}`);
  } catch (err) {
    if (!ALLOW_MOCK || !isOfflineError(err)) throw err;
    console.warn('[api] getRender fell back to mock:', err);
    return mockRender(renderId);
  }
}

/** GET /artifacts/{id}/download — signed attachment URL (saves the file to disk). */
export async function getDownloadUrl(artifactId: string): Promise<DownloadUrl> {
  try {
    return await request<DownloadUrl>(
      `/artifacts/${encodeURIComponent(artifactId)}/download`,
    );
  } catch (err) {
    if (!ALLOW_MOCK || !isOfflineError(err)) throw err;
    console.warn('[api] getDownloadUrl fell back to mock:', err);
    return mockDownloadUrl(artifactId);
  }
}

/** GET /artifacts/{id}/preview — signed inline URL for in-page <video> streaming. */
export async function getPreviewUrl(artifactId: string): Promise<DownloadUrl> {
  try {
    return await request<DownloadUrl>(
      `/artifacts/${encodeURIComponent(artifactId)}/preview`,
    );
  } catch (err) {
    if (!ALLOW_MOCK || !isOfflineError(err)) throw err;
    console.warn('[api] getPreviewUrl fell back to mock:', err);
    return mockDownloadUrl(artifactId);
  }
}
