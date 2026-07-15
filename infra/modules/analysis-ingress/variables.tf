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

variable "work_bucket" {
  type        = string
  description = "Work bucket name; chat_starter writes chatlog.v1 + timeline.v1."
}

variable "output_bucket" {
  type        = string
  description = "Output bucket name (passed through to chat_starter env)."
}

variable "render_state_machine_arn" {
  type        = string
  description = "Render workflow SM ARN the chat_starter StartExecutions."
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
