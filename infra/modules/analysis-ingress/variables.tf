variable "name" {
  type        = string
  description = "Resource name prefix, e.g. lang-live-analysis-dev."
}

variable "image_uri" {
  type        = string
  description = "Backend ECR container image (…:lambda) for the Starter Lambda."
}

variable "env" {
  type = string
}

variable "raw_bucket" {
  type        = string
  description = "Raw bucket name; EventBridge watches its source/ uploads."
}

variable "dynamodb_table" {
  type = string
}

variable "table_arn" {
  type = string
}

variable "state_machine_arn" {
  type        = string
  description = "Analysis workflow state machine ARN the Starter StartExecutions."
}

variable "tags" {
  type    = map(string)
  default = {}
}
