variable "name" {
  type        = string
  description = "Base name for the ECR repo and the (vestigial) App Runner IAM roles."
}

variable "backend_image" {
  type        = string
  description = "Container image URI. A public image (public.ecr.aws/...) works before the real image exists; a private ECR URI is used for real deploys."
}

variable "container_port" {
  type        = number
  default     = 8080
  description = "Port the FastAPI container listens on."
}

variable "health_check_path" {
  type        = string
  default     = "/health"
  description = "HTTP health check path App Runner polls."
}

variable "cpu" {
  type        = string
  default     = "1024"
  description = "App Runner vCPU units (1024 = 1 vCPU)."
}

variable "memory" {
  type        = string
  default     = "2048"
  description = "App Runner memory in MB."
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "Extra tags merged onto every resource."
}
