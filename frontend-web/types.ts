// TypeScript types mirroring contracts/openapi.yaml (浪 LIVE Editor API v0.4.0).
// Project / millisecond model (M1). Source of truth: ../contracts/openapi.yaml
// and ../contracts/*.v1.schema.json — keep in sync when the contract changes.
// All times are integers in milliseconds (ms). Core entity is Project (not job).

/** Project lifecycle states (openapi: ProjectState; demand.md §十八). */
export type ProjectState =
  | 'CREATED'
  | 'UPLOAD_PENDING'
  | 'UPLOADING'
  | 'ANALYZING'
  | 'COMPOSING'
  | 'READY_TO_EDIT'
  | 'RENDER_REQUESTED'
  | 'RENDERING'
  | 'ARTIFACT_READY'
  | 'FAILED';

/** Render lifecycle states (openapi: RenderState; demand.md §十八). */
export type RenderState =
  | 'CREATED'
  | 'PLANNING_SUBTITLES'
  | 'PLANNING_EFFECTS'
  | 'QUEUED'
  | 'RENDERING'
  | 'VALIDATING'
  | 'PUBLISHING'
  | 'SUCCEEDED'
  | 'FAILED';

/** Output aspect ratio (openapi: Timeline.aspect_ratio / Artifact.aspect_ratio). */
export type AspectRatio = '16:9' | '9:16' | '1:1';

/** Highlight analysis source (openapi: ProjectCreate.analysis_source). */
export type AnalysisSource = 'transcribe' | 'chat';

/** Request body for POST /projects (openapi: ProjectCreate). */
export interface ProjectCreate {
  title?: string;
  /** Final clip length in ms. 1000–60000 (≤ 60s). */
  target_duration_ms: number;
  /**
   * Highlight source. 'transcribe' (default): video-audio Transcribe pipeline
   * auto-runs after upload. 'chat': highlights come from the uploaded chat LOG
   * via POST /analyze; the Starter skips auto-Transcribe.
   */
  analysis_source?: AnalysisSource;
}

/** Response of POST /projects (openapi: ProjectCreated). */
export interface ProjectCreated {
  project_id: string;
  status: ProjectState;
  target_duration_ms: number;
  /** Allocated Raw-bucket object key (object exists only after upload). */
  source_key: string;
}

/** Response of GET /projects/{id} (openapi: Project). */
export interface Project {
  project_id: string;
  status: ProjectState;
  title?: string;
  target_duration_ms: number;
  analysis_source?: AnalysisSource;
  source_duration_ms?: number;
  source_key?: string;
  /** 影片 0:00 對應的 epoch 毫秒（來自 MP4 OBS creation_time）；chat epoch ↔ 影片相對毫秒 換算基準。 */
  video_start_epoch_ms?: number;
  latest_timeline_version?: number;
  latest_render_id?: string;
  latest_artifact_id?: string;
  created_at?: string;
  updated_at?: string;
  error_code?: string;
  error_message?: string;
}

/** Request body for POST /projects/{id}/upload-session (openapi: UploadSessionCreate). */
export interface UploadSessionCreate {
  filename: string;
  content_type?: string;
  /** multipart part count (frontend derives from file size); or provide size_bytes. */
  part_count?: number;
  size_bytes?: number;
}

/** One multipart part's presigned PUT URL (openapi: UploadPart). */
export interface UploadPart {
  part_number: number;
  url: string;
}

/** Response of POST /projects/{id}/upload-session (openapi: UploadSession). */
export interface UploadSession {
  upload_id: string;
  bucket: string;
  key: string;
  parts: UploadPart[];
  expires_in_sec?: number;
}

/** 偵測來源訊號（highlights.v1 signal）。 */
export type HighlightSignal = "chat_volume" | "speech_emotion" | "fusion";

/** 高光產出狀態（highlights.v1 status）。 */
export type HighlightStatus = "candidate" | "included" | "excluded" | "shifted";

/** Level-1 情緒分數與分項拆解（highlights.v1 emotion）。 */
export interface HighlightEmotion {
  score: number;
  breakdown?: { keyword?: number; emoji?: number; punctuation?: number; volume?: number };
  counts?: Record<string, number>;
}

/** 聊天窗 → 事件窗 的人工/AI 修正（highlights.v1 correction）。 */
export interface HighlightCorrection {
  applied?: boolean;
  /** 事件窗相對聊天窗位移；往前抓為負（如 -20000）、延後為正。 */
  offset_ms?: number;
  corrected_by?: string | null;
  corrected_at?: string | null;
  note?: string | null;
}

/** A single highlight candidate (openapi: Highlight; projection of highlights.v1). */
export interface Highlight {
  highlight_id: string;
  start_ms: number;
  end_ms: number;
  score: number;
  reason?: string;
  transcript?: string;
  suggested_title?: string;
  source_segment_ids?: string[];
  selected?: boolean;
  locked?: boolean;
  // chat-first additive fields
  signal?: HighlightSignal;
  status?: HighlightStatus;
  excluded_reason?: string | null;
  description?: string | null;
  /** 原始『聊天熱區』偵測窗（影片相對毫秒）；start_ms/end_ms 為修正後事件窗。 */
  chat_window?: { start_ms: number; end_ms: number };
  correction?: HighlightCorrection;
  emotion?: HighlightEmotion;
  detection?: {
    minute_volume?: number;
    baseline_mean?: number;
    baseline_sigma?: number;
    threshold?: number;
  };
  provenance?: Record<string, unknown>;
}

/** Response of GET /projects/{id}/highlights (openapi: HighlightList). */
export interface HighlightList {
  project_id: string;
  source_duration_ms?: number;
  highlights: Highlight[];
}

/** 5 維度標註類型（annotations.v1）：埋梗/反應-一開始/反應-轉折/笑點爆點/聊天室精彩留言。 */
export type AnnotationDimension =
  | "setup"
  | "reaction_start"
  | "reaction_turn"
  | "punchline"
  | "chat_highlights";

/** 單一維度標註 span（annotations.v1 dimension_span）；時間為絕對影片相對毫秒。 */
export interface DimensionSpan {
  dimension: AnnotationDimension;
  start_ms: number;
  end_ms: number;
  text?: string | null;
  /** chat_highlights 維度：挑選的聊天室精彩留言。 */
  messages?: { message_id?: string | null; username?: string | null; text: string }[];
}

/** 敘事節拍 cut-list 的一刀（annotations.v1 beat）。 */
export interface Beat {
  order: number;
  beat?: AnnotationDimension | null;
  line?: string | null;
  start_ms: number;
  end_ms: number;
  duration_ms?: number | null;
}

/** 單一高光的結構化標註（annotations.v1 annotation）。 */
export interface Annotation {
  highlight_id: string;
  title?: string | null;
  description?: string | null;
  dimensions: DimensionSpan[];
  beats?: Beat[];
  corrected_by?: string | null;
  corrected_at?: string | null;
}

/** Response of GET /projects/{id}/annotations (openapi: Annotations; projection of annotations.v1). */
export interface Annotations {
  project_id: string;
  annotation_version?: string | null;
  annotations: Annotation[];
}

/** Response of POST /projects/{id}/chat-upload (openapi: ChatUploadUrl). */
export interface ChatUploadUrl {
  bucket: string;
  key: string;
  url: string;
  expires_in_sec: number;
}

/** Request body for POST /projects/{id}/analyze (openapi: AnalyzeRequest). */
export interface AnalyzeRequest {
  chat_key?: string;
  /** 影片 0:00 的 epoch 毫秒；未連結影片時可省略，退回聊天相對時間模式。 */
  video_start_epoch_ms?: number;
  source_duration_ms?: number;
  params?: Record<string, unknown>;
}

/** Response of POST /projects/{id}/analyze (openapi: AnalyzeResult). */
export interface AnalyzeResult {
  project_id: string;
  status: ProjectState;
  highlight_count: number;
  analysis_version: string;
  source_duration_ms?: number;
}

/** Request body for PUT /projects/{id}/video-timebase (openapi: VideoTimebaseRequest). */
export interface VideoTimebaseRequest {
  video_start_epoch_ms?: number;
  /** MP4 OBS creation_time（ISO-8601，可含奈秒/時區）；伺服器換算成 epoch 毫秒。 */
  creation_time?: string;
  source_duration_ms?: number;
}

/** Request body for PATCH /projects/{id}/highlights/{highlight_id} (openapi: HighlightPatch). */
export interface HighlightPatch {
  /** 事件窗相對目前窗的位移；往前抓為負（如 -20000）、延後為正。累加進 correction.offset_ms。 */
  correction_offset_ms?: number;
  /** true=排除此段（如開場白）、false=取消排除。 */
  exclude?: boolean;
  selected?: boolean;
  locked?: boolean;
  note?: string;
}

/** One clip in a timeline (openapi: TimelineClip; projection of timeline.v1). */
export interface TimelineClip {
  timeline_order: number;
  highlight_id: string;
  source_start_ms: number;
  source_end_ms: number;
  timeline_start_ms: number;
  timeline_end_ms: number;
}

/** Subtitle settings (contract: free-form object; sample: { enabled, mode }). */
export interface SubtitleSettings {
  enabled: boolean;
  mode?: string;
}

/** Effect settings (contract: free-form object; sample: { enabled, intensity }). */
export interface EffectSettings {
  enabled: boolean;
  intensity?: 'low' | 'medium' | 'high';
}

/** GET/PUT /projects/{id}/timeline (openapi: Timeline; projection of timeline.v1). */
export interface Timeline {
  schema_version?: string;
  project_id: string;
  version: number;
  target_duration_ms: number;
  actual_duration_ms: number;
  aspect_ratio?: AspectRatio;
  subtitle_settings?: SubtitleSettings;
  effect_settings?: EffectSettings;
  clips: TimelineClip[];
}

/** One completed multipart part with its S3 ETag (openapi: UploadPartETag). */
export interface UploadPartETag {
  part_number: number;
  /** S3 part PUT ETag (may be a quoted string, e.g. `"abc…"`). */
  etag: string;
}

/** Request body for POST /projects/{id}/upload-session/complete (openapi: UploadCompleteRequest). */
export interface UploadCompleteRequest {
  upload_id: string;
  parts: UploadPartETag[];
}

/** Response of POST /projects/{id}/upload-session/complete (openapi: UploadCompleted). */
export interface UploadCompleted {
  project_id: string;
  status: ProjectState;
  /** Materialized Raw-bucket object key (source.mp4 now exists). */
  key: string;
}

/** Request body for POST /projects/{id}/compose (openapi: ComposeRequest). */
export interface ComposeRequest {
  target_duration_ms?: number;
  locked_highlight_ids?: string[];
  excluded_highlight_ids?: string[];
}

/** Response of PUT /projects/{id}/timeline and POST /projects/{id}/compose. */
export interface TimelineVersionResponse {
  timeline_version: number;
}

/** Request body for POST /projects/{id}/renders (openapi: RenderCreate). */
export interface RenderCreate {
  /** Omit to use the project's latest timeline version. */
  timeline_version?: number;
}

/** Response of POST /projects/{id}/renders — 202 (openapi: RenderCreated). */
export interface RenderCreated {
  render_id: string;
  status: RenderState;
}

/** Response of GET /renders/{render_id} (openapi: Render). */
export interface Render {
  render_id: string;
  project_id: string;
  status: RenderState;
  /** Human-facing stage label while running (e.g. PLANNING_SUBTITLES). */
  current_stage?: string;
  timeline_version?: number;
  artifact_id?: string;
  error_code?: string;
  error_message?: string;
  created_at?: string;
  started_at?: string;
  completed_at?: string;
}

/** Response of GET /artifacts/{artifact_id}/download (openapi: DownloadUrl). */
export interface DownloadUrl {
  url: string;
  expires_in_sec?: number;
}

/** Project states before the editor is usable — drive "processing" copy + polling. */
export const PROCESSING_STATES: ReadonlySet<ProjectState> = new Set<ProjectState>([
  'CREATED',
  'UPLOAD_PENDING',
  'UPLOADING',
  'ANALYZING',
  'COMPOSING',
]);

/** Project states where the four-region editor is usable. */
export const EDITABLE_STATES: ReadonlySet<ProjectState> = new Set<ProjectState>([
  'READY_TO_EDIT',
  'RENDER_REQUESTED',
  'RENDERING',
  'ARTIFACT_READY',
]);

/** Terminal Project states — status polling stops once one is reached. */
export const TERMINAL_STATES: ReadonlySet<ProjectState> = new Set<ProjectState>([
  'READY_TO_EDIT',
  'ARTIFACT_READY',
  'FAILED',
]);

/** Project states where getProject should keep polling (async backend work in flight). */
export const POLLABLE_PROJECT_STATES: ReadonlySet<ProjectState> = new Set<ProjectState>([
  'UPLOADING',
  'ANALYZING',
  'COMPOSING',
  'RENDER_REQUESTED',
  'RENDERING',
]);

/** Terminal Render states — render polling stops once one is reached. */
export const RENDER_TERMINAL_STATES: ReadonlySet<RenderState> = new Set<RenderState>([
  'SUCCEEDED',
  'FAILED',
]);
