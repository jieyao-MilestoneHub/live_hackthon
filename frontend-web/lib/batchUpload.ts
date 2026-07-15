// Batch upload orchestration for the 浪 LIVE Editor (contracts/openapi.yaml v0.5.0).
//
// The upload path is UNIFIED: a single file is just a batch of one. Each selected
// file becomes its OWN Project (N files = N projects), so the existing per-project
// analysis pipeline (S3 source.mp4 → EventBridge → Step Functions) runs unchanged.
//
// Concurrency model for high load (DEMO: many users, each up to 10GB):
//   fileConcurrency (3) files upload at once, each with partConcurrency (2) parts
//   in flight → a global ceiling of ~6 simultaneous PUTs. Browsers cap ~6 sockets
//   per host and every part hits the same S3 bucket host, so going higher only
//   queues and burns memory (≈ 16MiB partSize × 6 ≈ 96MB in flight).

import { createProject, createUploadSession, uploadToS3 } from './api';
import type { AnalysisSource } from '@/types';

/** Per-file caps — MUST mirror the server (backend-api/app/settings.py + main.py). */
export const MAX_UPLOAD_BYTES = 10 * 1024 ** 3; // 10 GB per file
export const MAX_BATCH_FILES = 20; // files per batch
export const ALLOWED_VIDEO_EXTS = ['.mp4', '.mov', '.mkv', '.webm', '.m4v'] as const;

/** True if the file passes the video type/extension check (content_type video/* OR allowed ext). */
export function isAllowedVideo(file: File): boolean {
  if (file.type && file.type.toLowerCase().startsWith('video/')) return true;
  const name = file.name.toLowerCase();
  return ALLOWED_VIDEO_EXTS.some((ext) => name.endsWith(ext));
}

/** Validate one file client-side. Returns an error message, or null if OK. */
export function validateFile(file: File): string | null {
  if (!isAllowedVideo(file)) {
    return `非支援的檔案型別（僅接受影片：${ALLOWED_VIDEO_EXTS.join(' / ')}）`;
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    const gb = (file.size / 1024 ** 3).toFixed(1);
    return `檔案過大（${gb}GB）；單檔上限 ${MAX_UPLOAD_BYTES / 1024 ** 3}GB`;
  }
  return null;
}

/** Split a picked FileList into accepted files and rejected {file, reason} pairs,
 * enforcing the per-batch file-count cap. `existingCount` lets you add to a list. */
export function partitionFiles(
  picked: File[],
  existingCount = 0,
): { accepted: File[]; rejected: { file: File; reason: string }[] } {
  const accepted: File[] = [];
  const rejected: { file: File; reason: string }[] = [];
  for (const file of picked) {
    if (existingCount + accepted.length >= MAX_BATCH_FILES) {
      rejected.push({ file, reason: `超過每批次 ${MAX_BATCH_FILES} 檔上限` });
      continue;
    }
    const err = validateFile(file);
    if (err) rejected.push({ file, reason: err });
    else accepted.push(file);
  }
  return { accepted, rejected };
}

export type BatchItemStatus =
  | 'queued'
  | 'creating'
  | 'uploading'
  | 'completing'
  | 'done'
  | 'failed';

export interface BatchItemState {
  status: BatchItemStatus;
  pct: number;
  projectId?: string;
  error?: string;
}

export interface BatchUploadShared {
  target_duration_ms: number;
  /** Batch is scoped to the video/transcribe pipeline (chat needs a paired CSV). */
  analysis_source: AnalysisSource;
  /** Optional prefix; each project is titled "<prefix> — <filename>" when set. */
  titlePrefix?: string;
}

export interface BatchItemResult {
  index: number;
  ok: boolean;
  projectId?: string;
  error?: string;
}

/**
 * Run `worker` over `items` with at most `concurrency` active at once. Never
 * rejects on a single failure — the worker owns its own try/catch; this just
 * bounds concurrency. Preserves natural completion order (results indexed by i).
 */
export async function mapWithConcurrency<T, R>(
  items: T[],
  concurrency: number,
  worker: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  const results: R[] = new Array(items.length);
  let next = 0;
  const run = async (): Promise<void> => {
    for (;;) {
      const i = next++;
      if (i >= items.length) return;
      results[i] = await worker(items[i], i);
    }
  };
  const lanes = Array.from({ length: Math.max(1, Math.min(concurrency, items.length)) }, () => run());
  await Promise.all(lanes);
  return results;
}

/**
 * Orchestrate a batch upload: each file → createProject → createUploadSession →
 * uploadToS3 (which finalizes + triggers analysis). Reports per-file state via
 * `onUpdate(index, patch)`. Individual failures are isolated (one bad file does
 * not abort the batch). Returns a result per file.
 */
export async function runBatchUpload(
  files: File[],
  shared: BatchUploadShared,
  onUpdate: (index: number, patch: Partial<BatchItemState>) => void,
  opts?: { fileConcurrency?: number; partConcurrency?: number; signal?: AbortSignal },
): Promise<BatchItemResult[]> {
  const fileConcurrency = opts?.fileConcurrency ?? 3;
  const partConcurrency = opts?.partConcurrency ?? 2;

  return mapWithConcurrency(files, fileConcurrency, async (file, index): Promise<BatchItemResult> => {
    if (opts?.signal?.aborted) {
      onUpdate(index, { status: 'failed', error: '已取消' });
      return { index, ok: false, error: '已取消' };
    }
    try {
      onUpdate(index, { status: 'creating', pct: 0 });
      const title = shared.titlePrefix?.trim()
        ? `${shared.titlePrefix.trim()} — ${file.name}`
        : file.name;
      const created = await createProject({
        title,
        target_duration_ms: shared.target_duration_ms,
        analysis_source: shared.analysis_source,
      });
      const projectId = created.project_id;

      onUpdate(index, { status: 'uploading', pct: 0, projectId });
      const session = await createUploadSession(projectId, {
        filename: file.name,
        content_type: file.type || 'video/mp4',
        size_bytes: file.size,
      });

      await uploadToS3(
        projectId,
        session,
        file,
        (pct) =>
          onUpdate(index, pct >= 100 ? { status: 'completing', pct: 100 } : { status: 'uploading', pct }),
        { partConcurrency, signal: opts?.signal },
      );

      onUpdate(index, { status: 'done', pct: 100, projectId });
      return { index, ok: true, projectId };
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      onUpdate(index, { status: 'failed', error: message });
      return { index, ok: false, error: message };
    }
  });
}
