variable "region" {
  type        = string
  default     = "ap-northeast-1"
  description = "AWS region (Tokyo)."
}

variable "project" {
  type        = string
  default     = "lang-live"
  description = "Project name; used as a prefix for resource names and tags."
}

variable "env" {
  type        = string
  default     = "dev"
  description = "Deployment environment."
}

variable "backend_image" {
  type = string
  # Placeholder public image so `terraform validate` and the first `apply`
  # succeed before the real backend image is built & pushed to ECR.
  # deploy.sh overrides this with the private ECR image URI (step 3):
  #   terraform apply -var backend_image=<ecr-url>:latest
  default     = "public.ecr.aws/docker/library/nginx:latest"
  description = "Container image URI for the App Runner backend service."
}
