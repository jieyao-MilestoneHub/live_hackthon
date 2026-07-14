variable "name" {
  type        = string
  description = "Resource name prefix, e.g. lang-live-render-dev."
}

variable "image_uri" {
  type        = string
  description = "Backend container image (…:lambda) for the plan/fail Lambdas."
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

variable "work_bucket" {
  type = string
}

variable "output_bucket" {
  type = string
}

variable "bucket_arns" {
  type = map(string)
}

variable "batch_job_queue_arn" {
  type = string
}

variable "batch_job_definition_arn" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}
