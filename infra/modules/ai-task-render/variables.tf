variable "name" {
  type        = string
  description = "Resource name prefix, e.g. lang-live-render-dev."
}

variable "image_uri" {
  type        = string
  description = "ffmpeg-in-Lambda render image (Dockerfile.render-lambda) ECR URI."
}

variable "env" {
  type = string
}

variable "ai_task_queue_arn" {
  type        = string
  description = "ai-task SQS queue ARN (from analysis-ingress) this Lambda consumes."
}

variable "dynamodb_table" {
  type = string
}

variable "table_arn" {
  type = string
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
  description = "Map raw/work/output -> bucket ARN for the S3 IAM policy."
}

variable "memory" {
  type        = number
  default     = 10240 # ≈6 vCPU; sized for encode speed, not RAM
  description = "Lambda memory (MB)."
}

variable "timeout" {
  type        = number
  default     = 900 # 15 min ceiling; a ≤60s output encodes in ~30–90s
  description = "Lambda timeout (seconds)."
}

variable "ephemeral_mb" {
  type        = number
  default     = 10240 # /tmp holds the whole streamed source.mp4 (multi-GB)
  description = "Ephemeral /tmp size (MB), 512–10240."
}

variable "reserved_concurrency" {
  type        = number
  default     = 5
  description = "Reserved concurrency cap (protects the account high-memory concurrency quota L-B99A9384)."
}

variable "ffmpeg_binary" {
  type        = string
  default     = "/usr/local/bin/ffmpeg"
  description = "Path to the static ffmpeg baked into the image."
}

variable "subtitle_fonts_dir" {
  type        = string
  default     = "/usr/share/fonts"
  description = "Directory libass scans for the CJK font (爆點字/字幕燒錄)."
}

variable "subtitle_font" {
  type        = string
  default     = "Noto Sans CJK TC"
  description = "ASS Default style font family name (must exist in the image)."
}

variable "tags" {
  type    = map(string)
  default = {}
}
