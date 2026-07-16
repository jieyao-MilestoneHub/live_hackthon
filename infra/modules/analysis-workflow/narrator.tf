# AI progress narration (#39) — gated Bedrock (Haiku). The narrator is invoked
# synchronously by step() inside the analysis worker Lambdas (see worker_env).
# Reuses bedrock_model_arns (already contains the Haiku fast model) for IAM.

variable "progress_narrator_llm" {
  type        = bool
  default     = false
  description = "Enable AI progress narration via Bedrock. OFF -> StubNarrator template (offline/CI safe). ON -> PROGRESS_NARRATOR_LLM=1 + a scoped bedrock:InvokeModel grant (reuses bedrock_model_arns)."
}

variable "progress_narrator_model_id" {
  type        = string
  default     = ""
  description = "Bedrock model / inference-profile id for narration (e.g. us.anthropic.claude-haiku-4-5-20251001-v1:0). Empty -> app default."
}

# Scoped narrator grant: only when narration is on AND ARNs are set. Reuses the
# edit-planner's bedrock_model_arns (Haiku is the fast model already listed there).
resource "aws_iam_role_policy" "worker_narrator_bedrock" {
  count = var.progress_narrator_llm && length(var.bedrock_model_arns) > 0 ? 1 : 0
  name  = "${var.name}-worker-narrator-bedrock"
  role  = aws_iam_role.worker.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "InvokeNarrator"
      Effect   = "Allow"
      Action   = ["bedrock:InvokeModel"]
      Resource = var.bedrock_model_arns
    }]
  })
}
