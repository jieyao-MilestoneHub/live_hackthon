// Display helpers. Times are milliseconds (ms) end-to-end (contract is ms);
// formatMs renders a readable m:ss.mmm while the UI also shows raw ms.

import type { ModerationStatus, ProjectState } from '@/types';

/** Format an integer millisecond value as `m:ss.mmm` (e.g. 150000 → "2:30.000"). */
export function formatMs(ms: number): string {
  const safe = Math.max(0, Math.round(ms));
  const totalSec = Math.floor(safe / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  const millis = safe % 1000;
  return `${m}:${s.toString().padStart(2, '0')}.${millis.toString().padStart(3, '0')}`;
}

/** Format a millisecond duration as seconds (e.g. 30000 → "30s"). */
export function msToSecondsLabel(ms: number): string {
  return `${Math.round(ms / 1000)}s`;
}

/** UI phase derived from Project status (demand.md §十八 → copy + gating). */
export interface ProjectPhase {
  /** User-facing status copy. */
  label: string;
  /** True while the backend is doing async work (show spinner/progress). */
  busy: boolean;
  /** True once the four-region editor is usable. */
  canEdit: boolean;
  /** True before any upload has started (show the file picker). */
  awaitingUpload: boolean;
}

const PHASES: Record<ProjectState, ProjectPhase> = {
  CREATED: { label: '待上傳原始影片', busy: false, canEdit: false, awaitingUpload: true },
  UPLOAD_PENDING: { label: '待上傳原始影片', busy: false, canEdit: false, awaitingUpload: true },
  UPLOADING: { label: '正在上傳', busy: true, canEdit: false, awaitingUpload: false },
  ANALYZING: { label: '正在分析高光', busy: true, canEdit: false, awaitingUpload: false },
  COMPOSING: { label: '正在建立初始剪輯', busy: true, canEdit: false, awaitingUpload: false },
  READY_TO_EDIT: { label: '可以開始編輯', busy: false, canEdit: true, awaitingUpload: false },
  RENDER_REQUESTED: { label: '已提交渲染', busy: true, canEdit: true, awaitingUpload: false },
  RENDERING: { label: '正在渲染影片', busy: true, canEdit: true, awaitingUpload: false },
  ARTIFACT_READY: { label: '影片已完成', busy: false, canEdit: true, awaitingUpload: false },
  FAILED: { label: '處理失敗', busy: false, canEdit: false, awaitingUpload: false },
};

export function projectPhase(status: ProjectState): ProjectPhase {
  return PHASES[status];
}

/** Badge style bucket for a Project status. */
export function badgeClass(status: ProjectState): string {
  if (status === 'READY_TO_EDIT' || status === 'ARTIFACT_READY') return 'badge done';
  if (status === 'FAILED') return 'badge failed';
  return 'badge running';
}

/** Content-moderation display copy + badge/severity bucket. */
export interface ModerationDisplay {
  label: string;
  /** 'ok' (allowed/overridden) | 'warn' (flagged/pending) | 'bad' (blocked). */
  tone: 'ok' | 'warn' | 'bad';
  /** True when the verdict forbids publishing (render/download gated). */
  gated: boolean;
}

const MODERATION_DISPLAY: Record<ModerationStatus, ModerationDisplay> = {
  PENDING: { label: '審核中', tone: 'warn', gated: true },
  ALLOWED: { label: '審核通過', tone: 'ok', gated: false },
  FLAGGED: { label: '已標記 · 待複核', tone: 'warn', gated: true },
  BLOCKED: { label: '審核未通過 · 已封鎖', tone: 'bad', gated: true },
  OVERRIDDEN: { label: '管理員已放行', tone: 'ok', gated: false },
};

export function moderationDisplay(status: ModerationStatus | undefined | null): ModerationDisplay {
  return status ? MODERATION_DISPLAY[status] : MODERATION_DISPLAY.PENDING;
}

/** True if the moderation verdict permits render/download (mirrors the backend gate). */
export function moderationAllowsPublish(status: ModerationStatus | undefined | null): boolean {
  return status === 'ALLOWED' || status === 'OVERRIDDEN';
}
