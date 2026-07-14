variable "name" {
  type        = string
  description = "Function name / resource name prefix."
}

variable "image_uri" {
  type        = string
  description = "ECR image URI (…:lambda) for the Lambda container."
}

variable "memory" {
  type        = number
  default     = 512
  description = "Lambda memory (MB)."
}

variable "timeout" {
  type        = number
  default     = 30
  description = "Lambda timeout (seconds)."
}

variable "tags" {
  type    = map(string)
  default = {}
}
