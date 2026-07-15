variable "name" {
  type        = string
  description = "Resource name prefix, e.g. lang-live-render-dev."
}

variable "image_uri" {
  type        = string
  description = "FFmpeg render container image (…:render)."
}

variable "env" {
  type = string
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
  description = "Map raw/work/output -> bucket ARN."
}

variable "max_vcpus" {
  type        = number
  default     = 8
  description = "Fargate compute environment max vCPUs (cost cap). At job_vcpu=2, this ÷ 2 = max concurrent render jobs. NOTE: this is only a ceiling — actual scaling is still bounded by the account's Fargate vCPU service quota (L-3032A538), so verify that quota before relying on a high value."
}

variable "ephemeral_storage_gib" {
  type        = number
  default     = 50
  description = "Fargate task ephemeral storage (GiB, 21–200). The default 20 GiB is tight for a 10GB source streamed to disk plus temp segments."
}

variable "job_vcpu" {
  type        = string
  default     = "1"
  description = "vCPU per render job (Fargate: 0.25/0.5/1/2/4)."
}

variable "job_memory" {
  type        = string
  default     = "2048"
  description = "Memory (MiB) per render job (must be valid for the vCPU)."
}

variable "tags" {
  type    = map(string)
  default = {}
}
