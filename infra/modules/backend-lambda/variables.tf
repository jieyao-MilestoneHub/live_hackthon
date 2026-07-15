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

# --- edit-by-language (sidecar) wiring -------------------------------------
# Two independent gates map to the feature's two routes:
#   enable_edit_by_language → async encode lane (SQS SendMessage + AI_TASK_QUEUE_URL)
#                             — Route A (deterministic Stub planner) works with just this.
#   edit_planner_llm        → flip to Route B (Claude on Bedrock): grants
#                             bedrock:InvokeModel + sets EDIT_PLANNER_LLM=1.
variable "enable_edit_by_language" {
  type        = bool
  default     = false
  description = "Wire the edit-by-language async encode lane: grant sqs:SendMessage to ai-task + set AI_TASK_QUEUE_URL."
}

variable "ai_task_queue_arn" {
  type        = string
  default     = ""
  description = "ai-task SQS queue ARN the sidecar enqueues encode jobs onto."
}

variable "ai_task_queue_url" {
  type        = string
  default     = ""
  description = "ai-task SQS queue URL (AI_TASK_QUEUE_URL env)."
}

variable "edit_planner_llm" {
  type        = bool
  default     = false
  description = "true = Route B (Claude on Bedrock). Requires bedrock_model_arns. false = Route A (Stub baseline)."
}

variable "edit_planner_model_id" {
  type        = string
  default     = ""
  description = "Bedrock Claude model id for model_tier=fast (e.g. anthropic.claude-haiku-4-5 or us.anthropic.claude-haiku-4-5). Confirm via probe."
}

variable "edit_planner_quality_model_id" {
  type        = string
  default     = ""
  description = "Bedrock Claude model id for model_tier=quality (e.g. (us.)anthropic.claude-sonnet-5)."
}

variable "bedrock_model_arns" {
  type        = list(string)
  default     = []
  description = "foundation-model / inference-profile ARNs the sidecar may InvokeModel. Required (non-empty) when edit_planner_llm=true. Set from the probe (add the regional foundation-model ARNs a us.* inference profile fans out to)."
}
