variable "project" {
  type        = string
  description = "Project name prefix for the Cognito user pool / client names."
}

variable "env" {
  type        = string
  description = "Deployment environment (dev/staging/prod)."
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "Extra tags merged onto the user pool (on top of the provider default_tags)."
}
