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

# --- edit-by-language planner (edit route; default Stub, no Bedrock) --------
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

# --- Batch-upload demo scaling knobs (see plan: 30 users × 10GB) ------------

variable "backend_reserved_concurrency" {
  type        = number
  default     = -1
  description = <<-EOT
    Reserved concurrent executions for the backend Lambda. -1 = unreserved (safe
    default). For the batch demo, AFTER verifying the account Lambda concurrency
    quota (Service Quotas L-B99A9384) has ample headroom, set this to ~40 so
    upload-session/complete calls can never be throttled by the pipeline Lambdas
    sharing the pool. WARNING: reserving concurrency fails apply if it would drop
    the account's unreserved pool below 100 — do not set on a low-cap account.
  EOT
}

variable "render_max_vcpus" {
  type        = number
  default     = 60
  description = "Fargate max vCPUs for the render compute environment. At job_vcpu=2 → up to 30 concurrent renders. Ceiling only; still bounded by the Fargate vCPU quota (L-3032A538) — verify before the demo."
}

variable "highlight_llm_enrich" {
  type        = bool
  default     = true
  description = "Bedrock title/reason enrichment for top highlights. Set false for the demo to drop ~150 concurrent converse calls off the critical path (the deterministic scorer still produces highlights)."
}

variable "alert_email" {
  type    = string
  default = ""
  # Email subscribed to the CloudWatch alarm SNS topic (DLQ depth, backend throttles,
  # SFN failures). Do NOT hardcode a real address here — this repo is public and that
  # would expose it as scrapable PII. Set it at deploy time via a gitignored
  # terraform.tfvars or `-var alert_email=...`. Empty (default) = create the topic but
  # no email subscription. AWS sends a one-time confirmation email that must be clicked.
  description = "Email subscribed to the alarm SNS topic. Leave unset in the repo; pass via terraform.tfvars or -var at deploy. Empty = no email subscription."
}

variable "moderation_enabled" {
  type        = bool
  default     = true
  description = "Content moderation (visual Rekognition + text Bedrock) + render/download gate. SCP: Rekognition content moderation is a NEW deployed-IAM surface — probe create→delete before relying on it (CLAUDE.md). Set false if Rekognition/Bedrock moderation isn't granted, so the pipeline marks ALLOWED and never gates."
}
