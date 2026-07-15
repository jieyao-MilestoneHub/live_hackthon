variable "name" {
  type        = string
  description = "Function name / resource name prefix."
}

variable "image_uri" {
  type        = string
  description = "ECR image URI (…:lambda) for the Lambda container."
}

variable "memory" {
  type        = number
  default     = 1024
  description = "Lambda memory (MB). 1024 so presigning hundreds of multipart part URLs (a 10GB file → ~640 parts) stays well within the API Gateway 30s window."
}

variable "timeout" {
  type        = number
  default     = 30
  description = "Lambda timeout (seconds)."
}

variable "reserved_concurrency" {
  type        = number
  default     = -1
  description = "Reserved concurrent executions for the backend. -1 = unreserved (default). Set ≥30 to guarantee upload-session/complete calls never throttle when the analysis pipeline saturates the shared account pool during a batch demo."
}

variable "tags" {
  type    = map(string)
  default = {}
}

# --- Data-plane wiring (M2.0: make the control plane real) -----------------
# Without these the Lambda has no env block -> app/settings.py defaults
# USE_INMEMORY=True and the wrong (short) resource names, so nothing persists.

variable "env" {
  type        = string
  default     = "dev"
  description = "Deployment environment; drives the ENV runtime var."
}

variable "use_inmemory" {
  type        = bool
  default     = false
  description = "true = in-process stores (offline). Deployed backend uses false to hit real DynamoDB/S3."
}

variable "dynamodb_table" {
  type        = string
  description = "VideoEditor DynamoDB table name the backend reads/writes (e.g. VideoEditor-dev)."
}

variable "table_arn" {
  type        = string
  description = "VideoEditor table ARN for the IAM policy (index/* is derived from it)."
}

variable "raw_bucket" {
  type        = string
  description = "Raw upload bucket name (presign + multipart)."
}

variable "work_bucket" {
  type        = string
  description = "Work bucket name (transcript/analysis/timelines/renders)."
}

variable "output_bucket" {
  type        = string
  description = "Output/artifact bucket name (presigned download)."
}

variable "bucket_arns" {
  type        = map(string)
  description = "Map raw/work/output -> bucket ARN, for the S3 IAM policy."
}

variable "presign_expiry_sec" {
  type        = number
  default     = 21600
  description = "Presigned URL expiry (seconds). 21600 (6h): a 10GB upload at ~15Mbps takes 90+ min, so the old 900s guaranteed mid-flight expiry. Bounded by the Lambda role's temp-credential lifetime."
}

variable "max_upload_bytes" {
  type        = number
  default     = 10737418240 # 10 * 1024^3 = 10 GiB
  description = "Per-file upload cap (bytes). create_upload_session returns 413 above this."
}

variable "max_batch_files" {
  type        = number
  default     = 20
  description = "Advisory per-batch file-count cap (enforced client-side; exposed for parity)."
}

variable "moderation_enabled" {
  type        = bool
  default     = true
  description = "Content-moderation feature flag. Gates render/download on the moderation verdict. Set false if Rekognition/Bedrock moderation isn't granted in the account."
}

variable "render_state_machine_arn" {
  type        = string
  default     = ""
  description = "Render workflow ARN. When set (env var), POST /renders StartExecutions it (async) instead of running Creative Planning inline."
}

variable "enable_render_start" {
  type        = bool
  default     = false
  description = "Create the states:StartExecution IAM policy for the render workflow. A static flag (not derived from render_state_machine_arn, whose value is unknown at plan time — count/for_each must be known at plan)."
}
