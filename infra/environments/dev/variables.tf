variable "region" {
  type        = string
  default     = "us-east-1"
  description = "AWS region (N. Virginia)."
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
  # Placeholder public image; the App Runner service that consumed this is
  # removed (SCP-blocked), but the ECR repo module still accepts the var.
  default     = "public.ecr.aws/docker/library/nginx:latest"
  description = "Container image URI (legacy App Runner var; unused at runtime)."
}

variable "backend_lambda_image" {
  type = string
  # Placeholder so validate/plan work before the :lambda image is pushed.
  # deploy overrides with the real ECR image: -var backend_lambda_image=<ecr>:lambda
  default     = "public.ecr.aws/lambda/python:3.11"
  description = "ECR image URI for the backend Lambda container."
}
