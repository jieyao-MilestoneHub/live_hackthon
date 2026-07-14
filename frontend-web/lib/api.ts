// Typed client for the 浪 LIVE Job API (contracts/openapi.yaml).
// Base URL from NEXT_PUBLIC_API_BASE_URL (baked at build time), default localhost.
// If a call fails (e.g. no backend running in dev), we fall back to local mock
// data so the walking-skeleton UI still renders.

import type { DownloadInfo, JobCreate, JobCreated, JobStatus } from '@/types';
import { mockJobStatus } from './mock';

export const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8080'
).replace(/\/$/, '');

/** Prefix used for jobs synthesized client-side when the backend is offline. */
const MOCK_JOB_PREFIX = 'mock_';

export function isMockJobId(jobId: string): boolean {
  return jobId.startsWith(MOCK_JOB_PREFIX);
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

/** POST /jobs — create a job. Falls back to a synthetic mock job when offline. */
export async function createJob(body: JobCreate): Promise<JobCreated> {
  try {
    return await request<JobCreated>('/jobs', {
      method: 'POST',
      body: JSON.stringify(body),
    });
  } catch (err) {
    console.warn('[api] createJob fell back to mock (backend unreachable):', err);
    return { job_id: `${MOCK_JOB_PREFIX}${Date.now()}`, status: 'CREATED' };
  }
}

/** GET /jobs/{job_id} — poll job status. Falls back to a SUCCEEDED mock when offline. */
export async function getJob(jobId: string): Promise<JobStatus> {
  try {
    return await request<JobStatus>(`/jobs/${encodeURIComponent(jobId)}`);
  } catch (err) {
    console.warn('[api] getJob fell back to mock (backend unreachable):', err);
    return mockJobStatus(jobId);
  }
}

/** GET /jobs/{job_id}/artifacts/{clip_id}/download — resolve a download URL. */
export async function getDownloadUrl(
  jobId: string,
  clipId: string,
): Promise<DownloadInfo> {
  try {
    return await request<DownloadInfo>(
      `/jobs/${encodeURIComponent(jobId)}/artifacts/${encodeURIComponent(clipId)}/download`,
    );
  } catch (err) {
    console.warn('[api] getDownloadUrl fell back to mock (backend unreachable):', err);
    // No real artifact in mock mode; caller detects the empty URL.
    return { url: '', expires_in_sec: 0 };
  }
}
