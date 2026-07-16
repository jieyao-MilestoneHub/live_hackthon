# Model-path flags for the chat-LOG ingress (chat_starter). Highlight enrichment
# reuses the existing Nova moderation grant (BedrockModerationInvoke); progress
# narration (Haiku) gets a scoped grant that reuses bedrock_model_arns.

variable "highlight_llm_enrich" {
  type        = bool
  default     = false
  description = "Chat-path highlight enrichment via Bedrock (Nova). OFF -> deterministic scorer. Nova is already granted for moderation, so no extra IAM."
}

variable "progress_narrator_llm" {
  type        = bool
  default     = false
  description = "Enable AI progress narration via Bedrock for chat_starter's step() calls. OFF -> StubNarrator template."
}

variable "progress_narrator_model_id" {
  type        = string
  default     = ""
  description = "Bedrock model / inference-profile id for narration. Empty -> app default."
}

variable "bedrock_model_arns" {
  type        = list(string)
  default     = []
  description = "inference-profile / foundation-model ARNs the narrator may InvokeModel. Required (non-empty) when progress_narrator_llm=true."
}

resource "aws_iam_role_policy" "chat_starter_narrator_bedrock" {
  count = var.progress_narrator_llm && length(var.bedrock_model_arns) > 0 ? 1 : 0
  name  = "${var.name}-chat-starter-narrator-bedrock"
  role  = aws_iam_role.chat_starter.id
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
