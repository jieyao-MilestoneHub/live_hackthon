variable "name" {
  type        = string
  description = "Resource/name prefix, e.g. lang-live-dev."
}

variable "region" {
  type        = string
  description = "AWS region (for metric widgets)."
}

variable "backend_function_name" {
  type        = string
  description = "Backend (control-plane) Lambda function name."
}

variable "worker_function_names" {
  type        = list(string)
  default     = []
  description = "Analysis worker Lambda function names."
}

variable "analysis_state_machine_arn" {
  type        = string
  description = "Analysis Step Functions state machine ARN."
}

variable "table_name" {
  type        = string
  description = "VideoEditor DynamoDB table name."
}

variable "intake_queue_name" {
  type        = string
  description = "analysis-intake SQS queue name."
}

variable "tags" {
  type    = map(string)
  default = {}
}
