variable "project" {
  type        = string
  description = "Project name prefix; used to keep S3 bucket names globally unique while preserving the docs/aws-infra.md §5 video-raw/work/output schema."
}

variable "env" {
  type        = string
  description = "Deployment environment (dev/staging/prod)."
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "Extra tags merged onto every resource (on top of the provider default_tags)."
}
