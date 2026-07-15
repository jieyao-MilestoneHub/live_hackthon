variable "name" {
  type        = string
  description = "Resource name prefix, e.g. lang-live-analysis-dev."
}

variable "image_uri" {
  type        = string
  description = "Backend ECR container image (…:lambda). Reused for every worker; the handler is chosen per-Lambda via image_config.command."
}

variable "env" {
  type        = string
  description = "Deployment environment (drives the ENV runtime var)."
}

variable "region" {
  type        = string
  description = "AWS region (for Bedrock model ARNs)."
}

variable "account_id" {
  type        = string
  description = "AWS account id (for Bedrock foundation-model ARNs)."
}

variable "dynamodb_table" {
  type        = string
  description = "VideoEditor table name."
}

variable "table_arn" {
  type        = string
  description = "VideoEditor table ARN (index/* derived)."
}

variable "raw_bucket" {
  type = string
}

variable "work_bucket" {
  type = string
}

variable "output_bucket" {
  type = string
}

variable "bucket_arns" {
  type        = map(string)
  description = "Map raw/work/output -> bucket ARN."
}

variable "highlight_llm_enrich" {
  type        = bool
  default     = true
  description = "Turn on real Bedrock title/reason enrichment in detect_highlights (fail-open if Bedrock model access is not granted)."
}

variable "moderation_enabled" {
  type        = bool
  default     = true
  description = "Run the content-moderation states (visual Rekognition + text Bedrock). Set false if those services aren't granted in the account (workers then mark ALLOWED and never block)."
}

variable "transcribe_poll_wait_sec" {
  type        = number
  default     = 30
  description = "Seconds the state machine waits between Amazon Transcribe status polls (Wait → GetTranscription loop)."
}

variable "worker_reserved_concurrency" {
  type        = number
  default     = -1
  description = "Reserved concurrent executions applied to EACH analysis worker Lambda. -1 = unreserved (default). Async transcribe keeps peak concurrency low, so leave -1 unless a low-cap account needs the pipeline pool fenced off (verify Service Quotas L-B99A9384 first)."
}

variable "bedrock_review_model_id" {
  type        = string
  default     = "amazon.nova-micro-v1:0"
  description = "Bedrock model used for highlight copy (matches app/aws/config nova_review_model_id)."
}

variable "tags" {
  type    = map(string)
  default = {}
}
