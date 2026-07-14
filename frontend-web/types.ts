// TypeScript types mirroring contracts/openapi.yaml (浪 LIVE Editor API v0.2.0).
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

/** Request body for POST /projects (openapi: ProjectCreate). */
export interface ProjectCreate {
  title?: string;
  /** Final clip length in ms. 1000–60000 (≤ 60s). */
  target_duration_ms: number;
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
  source_duration_ms?: number;
  source_key?: string;
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

/** A single highlight candidate (openapi: Highlight; projection of highlights.v1). */
export interface Highlight {
  highlight_id: string;
  start_ms: number;
  end_ms: number;
  score: number;
  reason?: string;
  transcript?: string;
  suggested_title?: string;
  selected?: boolean;
  locked?: boolean;
}

/** Response of GET /projects/{id}/highlights (openapi: HighlightList). */
export interface HighlightList {
  project_id: string;
  source_duration_ms?: number;
  highlights: Highlight[];
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
