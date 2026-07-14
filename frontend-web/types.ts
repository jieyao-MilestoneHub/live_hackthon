// TypeScript types mirroring contracts/openapi.yaml (浪 LIVE Job API v0.1.0).
// Source of truth: ../contracts/openapi.yaml — keep in sync when the contract changes.

/** Lifecycle states for a job (openapi: JobState). */
export type JobState =
  | 'CREATED'
  | 'UPLOAD_PENDING'
  | 'UPLOADED'
  | 'QUEUED'
  | 'VALIDATING'
  | 'TRANSCRIBING'
  | 'ANALYZING'
  | 'RENDERING'
  | 'FINALIZING'
  | 'SUCCEEDED'
  | 'FAILED'
  | 'CANCELLED';

/** Request body for POST /jobs (openapi: JobCreate). */
export interface JobCreate {
  filename: string;
  content_type?: string;
  tenant_id?: string;
}

/** S3 presigned multipart upload info returned with a created job. */
export interface UploadInfo {
  method?: string;
  url?: string;
  key?: string;
}

/** Response of POST /jobs (openapi: JobCreated). */
export interface JobCreated {
  job_id: string;
  status: JobState;
  upload?: UploadInfo;
}

/** A single highlight clip (openapi: Clip; maps to highlights.v1 highlight). */
export interface Clip {
  clip_id: string;
  start_sec: number;
  end_sec: number;
  score?: number;
  reason?: string;
  title?: string;
  download_ready?: boolean;
}

/** Response of GET /jobs/{job_id} (openapi: JobStatus). */
export interface JobStatus {
  job_id: string;
  status: JobState;
  current_stage?: string;
  progress?: number;
  highlights?: Clip[];
  error_code?: string;
  error_message?: string;
}

/** Response of GET /jobs/{job_id}/artifacts/{clip_id}/download. */
export interface DownloadInfo {
  url: string;
  expires_in_sec?: number;
}

/** Terminal states — polling stops once the job reaches one of these. */
export const TERMINAL_STATES: ReadonlySet<JobState> = new Set<JobState>([
  'SUCCEEDED',
  'FAILED',
  'CANCELLED',
]);
