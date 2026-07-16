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

variable "alert_email" {
  type        = string
  default     = ""
  description = "Email subscribed to the alarm SNS topic (DLQ depth, throttles, SFN failures). Empty = create the topic but no subscription. AWS sends a one-time confirmation email that must be clicked."
}

variable "dlq_queue_names" {
  type        = list(string)
  default     = []
  description = "DLQ queue names to alarm on (ApproximateNumberOfMessagesVisible > 0). A non-empty DLQ means a record was dropped after all retries — nobody consumes it, so the alarm is the only signal."
}

variable "tags" {
  type    = map(string)
  default = {}
}
