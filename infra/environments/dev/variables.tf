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
  description = "ECR image URI for the backend Lambda container (also reused for the analysis/render worker Lambdas)."
}

variable "render_image" {
  type = string
  # Placeholder so validate/plan work before the :render image is pushed.
  # deploy overrides with the real ECR image: -var render_image=<render-ecr>:render
  default     = "public.ecr.aws/docker/library/busybox:latest"
  description = "ECR image URI for the FFmpeg render Batch container."
}

# --- edit-by-language (default OFF; enable after Bedrock probe) -------------
variable "enable_edit_by_language" {
  type        = bool
  default     = false
  description = "Create the ai-task-render consumer + grant the sidecar sqs:SendMessage/AI_TASK_QUEUE_URL. Enable after pushing Dockerfile.render-lambda."
}

variable "render_lambda_image" {
  type        = string
  default     = "public.ecr.aws/lambda/python:3.11"
  description = "ffmpeg-in-Lambda render image (Dockerfile.render-lambda). deploy: -var render_lambda_image=<render-ecr>:lambda"
}

variable "edit_planner_llm" {
  type        = bool
  default     = false
  description = "true = Route B (Claude on Bedrock); false = Route A (deterministic Stub). Requires bedrock_model_arns when true."
}

variable "edit_planner_model_id" {
  type        = string
  default     = ""
  description = "Bedrock Claude id for model_tier=fast (e.g. (us.)anthropic.claude-haiku-4-5). From the probe."
}

variable "edit_planner_quality_model_id" {
  type        = string
  default     = ""
  description = "Bedrock Claude id for model_tier=quality (e.g. (us.)anthropic.claude-sonnet-5). From the probe."
}

variable "bedrock_model_arns" {
  type        = list(string)
  default     = []
  description = "foundation-model / inference-profile ARNs the sidecar may InvokeModel (required when edit_planner_llm=true)."
}
