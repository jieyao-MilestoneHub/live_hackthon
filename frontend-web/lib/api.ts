// Typed client for the 浪 LIVE Editor API (contracts/openapi.yaml v0.2.0).
// Base URL from NEXT_PUBLIC_API_BASE_URL (baked at build time). If a call fails
// — backend unreachable, or a not-yet-built endpoint returns 501 — we fall back
// to local mock data so the editor skeleton still renders in dev.
//
// Swapping in the real backend later needs no page changes: this file + types.ts
// are the only contract-facing surface.

import type {
  HighlightList,
  Project,
  ProjectCreate,
  ProjectCreated,
  Timeline,
  UploadSession,
  UploadSessionCreate,
} from '@/types';
import {
  markMockAnalysisStart,
  mockHighlightList,
  mockProject,
  mockProjectStatusFor,
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
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
 * Browser direct upload to S3 Raw bucket using the session's presigned parts.
 * Splits the file evenly across parts and PUTs each with progress.
 *
 * NOTE: CompleteMultipartUpload (submitting collected ETags) is not yet in the
 * contract — the backend/infra completes the multipart upload (or auto-completes
 * via a single part) for now. Tracked for the next contract iteration.
 */
export async function uploadToS3(
  session: UploadSession,
  file: File,
  onProgress?: (pct: number) => void,
): Promise<void> {
  const isMock =
    session.upload_id.startsWith('mock_upload_') ||
    session.parts.some((p) => p.url.startsWith('https://mock.local'));
  if (isMock) {
    await simulateUpload(onProgress);
    return;
  }

  const parts = [...session.parts].sort((a, b) => a.part_number - b.part_number);
  const partSize = Math.ceil(file.size / parts.length);
  const etags: string[] = [];
  let uploadedBytes = 0;

  for (const part of parts) {
    const start = (part.part_number - 1) * partSize;
    const blob = file.slice(start, Math.min(start + partSize, file.size));
    const etag = await putPart(part.url, blob, (loaded) => {
      const pct = file.size > 0 ? ((uploadedBytes + loaded) / file.size) * 100 : 100;
      onProgress?.(Math.min(100, Math.round(pct)));
    });
    etags.push(etag);
    uploadedBytes += blob.size;
  }
  onProgress?.(100);
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
