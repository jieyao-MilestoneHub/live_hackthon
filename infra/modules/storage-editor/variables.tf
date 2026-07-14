variable "project" {
  type        = string
  description = "Project name prefix; keeps the globally-unique S3 bucket names distinct while preserving the demand.md §16 video-editor-{raw,work,output} schema."
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
