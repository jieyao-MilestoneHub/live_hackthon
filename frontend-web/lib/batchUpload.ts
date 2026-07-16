// Batch upload orchestration for the 浪 LIVE Editor (contracts/openapi.yaml v0.7.0).
//
// The upload path is UNIFIED: a single item is just a batch of one. Each item
// becomes its OWN Project (N items = N projects), so the existing per-project
// analysis pipeline (S3 source.mp4 → EventBridge → Step Functions) runs unchanged.
//
// Each item = { video, log?, prompt? }:
//   - log present  → analysis_source 'chat' (彈幕熱度): upload video, link timebase,
//     then upload chat.csv which auto-triggers analyze → compose → render.
//   - log absent   → analysis_source 'transcribe': upload video auto-triggers analysis.
//   - prompt (剪接指令) is saved client-side (saveEditIntent) to drive the agent /
//     指令版 track later; blank → 指令版 falls back to the default template.
//
// Concurrency model for high load (DEMO: many users, each up to 10GB):
//   fileConcurrency (3) items upload at once, each with partConcurrency (2) parts
//   in flight → a global ceiling of ~6 simultaneous PUTs. Browsers cap ~6 sockets
//   per host and every part hits the same S3 bucket host, so going higher only
//   queues and burns memory (≈ 16MiB partSize × 6 ≈ 96MB in flight).

import {
  createChatUpload,
  createProject,
  createUploadSession,
  setVideoTimebase,
  uploadChatCsv,
  uploadToS3,
} from './api';
import { saveEditIntent } from './editIntent';
import { readVideoDurationMs } from './media';
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
  /** Derived analysis source (chat when a LOG is paired, else transcribe). */
  analysisSource?: AnalysisSource;
}

/** One batch item: a required video, an optional paired chat LOG, and an
 * optional 剪接指令 (natural-language prompt) that drives the 指令版 track. */
export interface BatchItemInput {
  video: File;
  log?: File;
  prompt?: string;
}

export interface BatchUploadShared {
  target_duration_ms: number;
  /** Optional prefix; each project is titled "<prefix> — <video filename>" when set. */
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
 * Orchestrate a batch upload: each item → createProject → createUploadSession →
 * uploadToS3 (finalizes + triggers analysis), and — when a LOG is paired — link
 * the video timebase and upload chat.csv (which auto-triggers the chat pipeline).
 * The 剪接指令 prompt is saved client-side to drive the 指令版 track. Reports
 * per-item state via `onUpdate(index, patch)`. Individual failures are isolated
 * (one bad item does not abort the batch). Returns a result per item.
 */
export async function runBatchUpload(
  items: BatchItemInput[],
  shared: BatchUploadShared,
  onUpdate: (index: number, patch: Partial<BatchItemState>) => void,
  opts?: { fileConcurrency?: number; partConcurrency?: number; signal?: AbortSignal },
): Promise<BatchItemResult[]> {
  const fileConcurrency = opts?.fileConcurrency ?? 3;
  const partConcurrency = opts?.partConcurrency ?? 2;

  return mapWithConcurrency(items, fileConcurrency, async (item, index): Promise<BatchItemResult> => {
    const { video, log, prompt } = item;
    const analysisSource: AnalysisSource = log ? 'chat' : 'transcribe';
    if (opts?.signal?.aborted) {
      onUpdate(index, { status: 'failed', error: '已取消', analysisSource });
      return { index, ok: false, error: '已取消' };
    }
    try {
      onUpdate(index, { status: 'creating', pct: 0, analysisSource });
      const title = shared.titlePrefix?.trim()
        ? `${shared.titlePrefix.trim()} — ${video.name}`
        : video.name;
      const created = await createProject({
        title,
        target_duration_ms: shared.target_duration_ms,
        analysis_source: analysisSource,
      });
      const projectId = created.project_id;
      // Persist the 剪接指令 (drives the 指令版 track); blank → cleared → 模板.
      saveEditIntent(projectId, prompt);

      onUpdate(index, { status: 'uploading', pct: 0, projectId });
      const session = await createUploadSession(projectId, {
        filename: video.name,
        content_type: video.type || 'video/mp4',
        size_bytes: video.size,
      });

      await uploadToS3(
        projectId,
        session,
        video,
        (pct) =>
          onUpdate(index, pct >= 100 ? { status: 'completing', pct: 100 } : { status: 'uploading', pct }),
        { partConcurrency, signal: opts?.signal },
      );

      // Paired LOG → chat pipeline: link timebase first (so auto-analysis sees
      // source_duration_ms), then upload chat.csv which auto-triggers the flow.
      if (log) {
        onUpdate(index, { status: 'completing', pct: 100 });
        const durationMs = await readVideoDurationMs(video);
        if (durationMs) await setVideoTimebase(projectId, { source_duration_ms: durationMs });
        const chatSession = await createChatUpload(projectId);
        await uploadChatCsv(chatSession, log);
      }

      onUpdate(index, { status: 'done', pct: 100, projectId });
      return { index, ok: true, projectId };
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      onUpdate(index, { status: 'failed', error: message });
      return { index, ok: false, error: message };
    }
  });
}
