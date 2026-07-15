// Batch upload orchestration for the 浪 LIVE Editor (contracts/openapi.yaml v0.7.0).
//
// Every upload item is a VIDEO + chat-room LOG CSV pair. Each pair becomes its
// OWN Project (analysis_source: 'chat'): the browser uploads the video via S3
// multipart, links the video timebase, then PUTs the LOG CSV — dropping chat.csv
// AUTO-triggers analyze → compose → render server-side, so the client never calls
// /analyze or /compose itself (they would 409 / race the auto-trigger).
//
// Concurrency model for high load (DEMO: many users, each up to 10GB):
//   fileConcurrency (3) pairs upload at once, each video with partConcurrency (2)
//   parts in flight → a global ceiling of ~6 simultaneous PUTs. Browsers cap ~6
//   sockets per host and every part hits the same S3 bucket host, so going higher
//   only queues and burns memory (≈ 16MiB partSize × 6 ≈ 96MB in flight).

import {
  createChatUpload,
  createProject,
  createUploadSession,
  setVideoTimebase,
  uploadChatCsv,
  uploadToS3,
} from './api';
import { readVideoDurationMs } from './media';

/** Per-file caps — MUST mirror the server (backend-api/app/settings.py + main.py). */
export const MAX_UPLOAD_BYTES = 10 * 1024 ** 3; // 10 GB per video
export const MAX_BATCH_FILES = 20; // videos (= pairs) per batch
export const ALLOWED_VIDEO_EXTS = ['.mp4', '.mov', '.mkv', '.webm', '.m4v'] as const;

/** LOG (chat-room CSV) caps. CSVs are small; this is a client sanity ceiling. */
export const ALLOWED_LOG_EXTS = ['.csv'] as const;
export const MAX_LOG_BYTES = 64 * 1024 * 1024; // 64 MB per LOG

/** True if the file passes the video type/extension check (content_type video/* OR allowed ext). */
export function isAllowedVideo(file: File): boolean {
  if (file.type && file.type.toLowerCase().startsWith('video/')) return true;
  const name = file.name.toLowerCase();
  return ALLOWED_VIDEO_EXTS.some((ext) => name.endsWith(ext));
}

/** True if the file looks like a chat LOG CSV (extension-first; CSV MIME varies by browser). */
export function isAllowedLog(file: File): boolean {
  return file.name.toLowerCase().endsWith('.csv');
}

/** Validate one video client-side. Returns an error message, or null if OK. */
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

/** Validate one LOG CSV client-side. Returns an error message, or null if OK. */
export function validateLog(file: File): string | null {
  if (!isAllowedLog(file)) {
    return `非支援的 LOG 型別（僅接受 ${ALLOWED_LOG_EXTS.join(' / ')}）`;
  }
  if (file.size > MAX_LOG_BYTES) {
    const mb = (file.size / (1024 * 1024)).toFixed(1);
    return `LOG 過大（${mb}MB）；上限 ${MAX_LOG_BYTES / (1024 * 1024)}MB`;
  }
  return null;
}

// --- Video ↔ LOG pairing -------------------------------------------------

/** Stable identity for a picked File — disambiguates duplicate filenames. */
export function fileId(f: File): string {
  return `${f.name}::${f.size}::${f.lastModified}`;
}

/**
 * Normalize a filename to a pairing key so a video and its LOG match: strip the
 * directory + final extension, lowercase, then repeatedly peel a trailing
 * chat/log marker ("_chat", "-log", "聊天", "彈幕", "danmaku") plus separators.
 * e.g. "ClipA.mp4" and "clipA_chat.csv" both → "clipa". Never returns "" (a name
 * that is entirely a marker, e.g. "log.csv", keeps the marker as its key).
 */
export function pairKey(filename: string): string {
  let base = (filename.split(/[\\/]/).pop() ?? filename).replace(/\.[^.]+$/, '');
  base = base.toLowerCase().trim();
  for (;;) {
    const next = base
      .replace(/[ _.-]*(chat|log|聊天室?|彈幕|danmaku)$/u, '')
      .replace(/[ _.-]+$/u, '');
    if (next === base || next === '') break;
    base = next;
  }
  return base;
}

export interface PairedItem {
  video: File;
  log: File | null;
}

export interface PairFilesResult {
  /** One entry per accepted video, in input order. */
  pairs: PairedItem[];
  /** Convenience view: videos still missing a LOG. */
  unpairedVideos: File[];
  /** LOGs consumed by no pair. */
  unpairedLogs: File[];
  /** Files that failed the type/size gate (only populated by splitPicked/pairFiles). */
  rejected: { file: File; reason: string }[];
}

/**
 * Split a freshly-picked FileList into accepted videos, accepted LOGs, and
 * rejected {file, reason} pairs. Enforces the per-batch video (= pair) cap; LOGs
 * are unbounded in count (still size-capped). `existingVideoCount` lets callers
 * append to an in-progress selection.
 */
export function splitPicked(
  picked: File[],
  existingVideoCount = 0,
  maxVideos = MAX_BATCH_FILES,
): { videos: File[]; logs: File[]; rejected: { file: File; reason: string }[] } {
  const videos: File[] = [];
  const logs: File[] = [];
  const rejected: { file: File; reason: string }[] = [];
  for (const file of picked) {
    if (isAllowedVideo(file)) {
      if (existingVideoCount + videos.length >= maxVideos) {
        rejected.push({ file, reason: `超過每批次 ${maxVideos} 對上限` });
        continue;
      }
      const err = validateFile(file);
      if (err) rejected.push({ file, reason: err });
      else videos.push(file);
    } else if (isAllowedLog(file)) {
      const err = validateLog(file);
      if (err) rejected.push({ file, reason: err });
      else logs.push(file);
    } else {
      rejected.push({ file, reason: '非支援的檔案型別（需影片或 .csv）' });
    }
  }
  return { videos, logs, rejected };
}

/**
 * Pair each video with a LOG. Manual assignments (videoFileId → logFileId, or ''
 * to force "no log") win; the remaining videos/LOGs are greedily auto-matched by
 * pairKey — a LOG matches at most one video, and on a key tie the earlier video
 * (input order) wins. This is the single source of truth the uploader renders.
 */
export function reconcilePairs(
  videos: File[],
  logs: File[],
  manual: Record<string, string> = {},
): PairFilesResult {
  const logById = new Map(logs.map((l) => [fileId(l), l] as const));
  const usedLogIds = new Set<string>();
  const pairs: PairedItem[] = new Array(videos.length);
  const autoNeeded: number[] = [];

  // Pass 1 — honour manual assignments (a stale/duplicate ref falls back to auto).
  videos.forEach((video, i) => {
    const vId = fileId(video);
    if (!Object.prototype.hasOwnProperty.call(manual, vId)) {
      autoNeeded.push(i);
      return;
    }
    const chosen = manual[vId];
    if (chosen === '') {
      pairs[i] = { video, log: null };
    } else if (logById.has(chosen) && !usedLogIds.has(chosen)) {
      usedLogIds.add(chosen);
      pairs[i] = { video, log: logById.get(chosen)! };
    } else {
      autoNeeded.push(i);
    }
  });

  // Pass 2 — greedy auto-match by pairKey over the still-unused LOGs.
  const logsByKey = new Map<string, File[]>();
  for (const log of logs) {
    if (usedLogIds.has(fileId(log))) continue;
    const k = pairKey(log.name);
    const arr = logsByKey.get(k);
    if (arr) arr.push(log);
    else logsByKey.set(k, [log]);
  }
  for (const i of autoNeeded) {
    const video = videos[i];
    const bucket = logsByKey.get(pairKey(video.name));
    let log: File | null = null;
    while (bucket && bucket.length) {
      const cand = bucket.shift()!;
      if (!usedLogIds.has(fileId(cand))) {
        usedLogIds.add(fileId(cand));
        log = cand;
        break;
      }
    }
    pairs[i] = { video, log };
  }

  return {
    pairs,
    unpairedVideos: pairs.filter((p) => p.log === null).map((p) => p.video),
    unpairedLogs: logs.filter((l) => !usedLogIds.has(fileId(l))),
    rejected: [],
  };
}

/** Split + auto-pair in one shot (used for the initial ingest / suggestion). */
export function pairFiles(picked: File[], opts?: { maxVideos?: number }): PairFilesResult {
  const { videos, logs, rejected } = splitPicked(picked, 0, opts?.maxVideos ?? MAX_BATCH_FILES);
  return { ...reconcilePairs(videos, logs, {}), rejected };
}

// --- Batch orchestration -------------------------------------------------

export type BatchItemStatus =
  | 'queued'
  | 'creating'
  | 'uploading'
  | 'completing'
  | 'linking'
  | 'starting'
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
  /** Optional prefix; each project is titled "<prefix> — <filename>" when set. */
  titlePrefix?: string;
}

/** One upload unit: a video paired with its chat-room LOG CSV. */
export interface ChatUploadPair {
  video: File;
  log: File;
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
 * Orchestrate a batch upload of video+LOG pairs. Each pair → createProject(chat)
 * → createUploadSession → uploadToS3 (video) → setVideoTimebase (non-fatal) →
 * createChatUpload → uploadChatCsv (which AUTO-triggers analysis + render). The
 * client never calls /analyze or /compose. Reports per-pair state via
 * `onUpdate(index, patch)`. Individual failures are isolated (one bad pair does
 * not abort the batch). Returns a result per pair.
 */
export async function runBatchUpload(
  pairs: ChatUploadPair[],
  shared: BatchUploadShared,
  onUpdate: (index: number, patch: Partial<BatchItemState>) => void,
  opts?: { fileConcurrency?: number; partConcurrency?: number; signal?: AbortSignal },
): Promise<BatchItemResult[]> {
  const fileConcurrency = opts?.fileConcurrency ?? 3;
  const partConcurrency = opts?.partConcurrency ?? 2;

  return mapWithConcurrency(pairs, fileConcurrency, async (pair, index): Promise<BatchItemResult> => {
    if (opts?.signal?.aborted) {
      onUpdate(index, { status: 'failed', error: '已取消' });
      return { index, ok: false, error: '已取消' };
    }
    const { video, log } = pair;
    try {
      // 1) Create the chat project.
      onUpdate(index, { status: 'creating', pct: 0 });
      const title = shared.titlePrefix?.trim()
        ? `${shared.titlePrefix.trim()} — ${video.name}`
        : video.name;
      const created = await createProject({
        title,
        target_duration_ms: shared.target_duration_ms,
        analysis_source: 'chat',
      });
      const projectId = created.project_id;

      // 2-3) Upload the video via S3 multipart (materializes source.mp4).
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

      // 4) Link the video timebase BEFORE the LOG so the auto-analysis sees
      //    source_duration_ms. Non-fatal: a failure just falls back to
      //    chat-relative timing (parity with the single-file chat flow).
      onUpdate(index, { status: 'linking', pct: 100, projectId });
      try {
        const durationMs = await readVideoDurationMs(video);
        if (durationMs) await setVideoTimebase(projectId, { source_duration_ms: durationMs });
      } catch (err) {
        console.warn('[batch] setVideoTimebase skipped:', err);
      }

      // 5) Upload the chat LOG — dropping chat.csv AUTO-triggers the whole
      //    pipeline (analyze → compose → render) server-side.
      onUpdate(index, { status: 'starting', pct: 100, projectId });
      const chatSession = await createChatUpload(projectId);
      await uploadChatCsv(chatSession, log);

      onUpdate(index, { status: 'done', pct: 100, projectId });
      return { index, ok: true, projectId };
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      onUpdate(index, { status: 'failed', error: message });
      return { index, ok: false, error: message };
    }
  });
}
