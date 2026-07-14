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
  default     = 512
  description = "Lambda memory (MB)."
}

variable "timeout" {
  type        = number
  default     = 30
  description = "Lambda timeout (seconds)."
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
  default     = 900
  description = "Presigned URL expiry (seconds)."
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
