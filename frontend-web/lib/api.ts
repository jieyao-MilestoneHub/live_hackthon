// Typed client for the 浪 LIVE Editor API (contracts/openapi.yaml v0.3.0).
// Base URL from NEXT_PUBLIC_API_BASE_URL (baked at build time). If a call fails
// — backend unreachable, or a not-yet-built endpoint returns 501 — we fall back
// to local mock data so the editor still drives a plausible flow in dev.
//
// Swapping in the real backend later needs no page changes: this file + types.ts
// are the only contract-facing surface. When a Cognito IdToken is present
// (lib/auth.ts) it is attached as `Authorization: Bearer <token>` on every call.

import type {
  ComposeRequest,
  DownloadUrl,
  HighlightList,
  Project,
  ProjectCreate,
  ProjectCreated,
  Render,
  RenderCreated,
  Timeline,
  TimelineVersionResponse,
  UploadCompleted,
  UploadPartETag,
  UploadSession,
  UploadSessionCreate,
} from '@/types';
import { getIdToken } from './auth';
import {
  markMockAnalysisStart,
  mockCreateRender,
  mockDownloadUrl,
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

/** Prefix for projects synthesized client-side when the backend is offline. */
const MOCK_PROJECT_PREFIX = 'mock_';

export function isMockProjectId(projectId: string): boolean {
  return projectId.startsWith(MOCK_PROJECT_PREFIX);
}

/** Base JSON headers plus `Authorization: Bearer <IdToken>` when logged in. */
function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  const token = getIdToken();
  if (token) headers['Authorization'] = `Bearer ${token}`;
  return headers;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { ...authHeaders(), ...((init?.headers as Record<string, string>) ?? {}) },
  });
  if (!res.ok) {
    throw new Error(`API ${init?.method ?? 'GET'} ${path} failed: ${res.status}`);
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
  try {
    return await request<UploadSession>(
      `/projects/${encodeURIComponent(projectId)}/upload-session`,
      { method: 'POST', body: JSON.stringify(body) },
    );
  } catch (err) {
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
): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', url);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onLoaded(e.loaded);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(xhr.getResponseHeader('ETag') ?? '');
      } else {
        reject(new Error(`part PUT failed: ${xhr.status}`));
      }
    };
    xhr.onerror = () => reject(new Error('part PUT network error'));
    xhr.send(blob);
  });
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
    console.warn('[api] completeUploadSession fell back to mock:', err);
    markMockAnalysisStart(projectId);
    return {
      project_id: projectId,
      status: 'ANALYZING',
      key: `tenant=demo/project=${projectId}/source/source.mp4`,
    };
  }
}

/**
 * Browser direct upload to the S3 Raw bucket using the session's presigned parts,
 * followed by the multipart-complete handshake.
 *
 * Splits the file evenly across parts, PUTs each with progress, collects the
 * per-part ETags (S3 returns them in the `ETag` response header — value is
 * usually double-quoted, which S3's CompleteMultipartUpload expects, so we keep
 * it verbatim and only trim whitespace), then calls completeUploadSession to
 * finalize the object and trigger analysis.
 */
export async function uploadToS3(
  projectId: string,
  session: UploadSession,
  file: File,
  onProgress?: (pct: number) => void,
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
  const completed: UploadPartETag[] = [];
  let uploadedBytes = 0;

  for (const part of parts) {
    const start = (part.part_number - 1) * partSize;
    const blob = file.slice(start, Math.min(start + partSize, file.size));
    const etag = await putPart(part.url, blob, (loaded) => {
      const pct = file.size > 0 ? ((uploadedBytes + loaded) / file.size) * 100 : 100;
      onProgress?.(Math.min(100, Math.round(pct)));
    });
    completed.push({ part_number: part.part_number, etag: (etag ?? '').trim() });
    uploadedBytes += blob.size;
  }
  onProgress?.(100);

  // Finalize the multipart upload — materializes source.mp4 + triggers analysis.
  await completeUploadSession(projectId, session.upload_id, completed);
}

/** GET /projects/{id} — poll project status. Falls back to a mock when offline. */
export async function getProject(projectId: string): Promise<Project> {
  try {
    return await request<Project>(`/projects/${encodeURIComponent(projectId)}`);
  } catch (err) {
    console.warn('[api] getProject fell back to mock (backend unreachable):', err);
    return mockProject(projectId, mockProjectStatusFor(projectId));
  }
}

/** GET /projects/{id}/highlights — highlight candidates (501 until M2 → mock). */
export async function getHighlights(projectId: string): Promise<HighlightList> {
  try {
    return await request<HighlightList>(
      `/projects/${encodeURIComponent(projectId)}/highlights`,
    );
  } catch (err) {
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
    console.warn('[api] composeTimeline fell back to mock:', err);
    return { timeline_version: 2 };
  }
}

/** POST /projects/{id}/renders — submit a render (202). Falls back to a mock render. */
export async function createRender(
  projectId: string,
  timelineVersion?: number,
): Promise<RenderCreated> {
  try {
    return await request<RenderCreated>(
      `/projects/${encodeURIComponent(projectId)}/renders`,
      {
        method: 'POST',
        body: JSON.stringify(
          timelineVersion != null ? { timeline_version: timelineVersion } : {},
        ),
      },
    );
  } catch (err) {
    console.warn('[api] createRender fell back to mock:', err);
    return mockCreateRender(projectId);
  }
}

/** GET /renders/{render_id} — poll render progress. Falls back to a mock render. */
export async function getRender(renderId: string): Promise<Render> {
  try {
    return await request<Render>(`/renders/${encodeURIComponent(renderId)}`);
  } catch (err) {
    console.warn('[api] getRender fell back to mock:', err);
    return mockRender(renderId);
  }
}

/** GET /artifacts/{id}/download — signed URL for the finished short video. */
export async function getDownloadUrl(artifactId: string): Promise<DownloadUrl> {
  try {
    return await request<DownloadUrl>(
      `/artifacts/${encodeURIComponent(artifactId)}/download`,
    );
  } catch (err) {
    console.warn('[api] getDownloadUrl fell back to mock:', err);
    return mockDownloadUrl(artifactId);
  }
}
