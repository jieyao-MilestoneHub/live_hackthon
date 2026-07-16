# Subtitle keyword-pop animation via Bedrock (Nova) in the FFmpeg render job.
# The render container's job role gets a scoped InvokeModel grant when enabled.
# The SUBTITLE_LLM_KEYWORDS env is set on the Batch job definition (main.tf).

variable "subtitle_llm_keywords" {
  type        = bool
  default     = false
  description = "Enable LLM-picked subtitle keyword animation (Bedrock Nova). OFF -> deterministic rule-based keywords."
}

variable "subtitle_model_arns" {
  type        = list(string)
  default     = []
  description = "foundation-model / inference-profile ARNs the subtitle keyword extractor may InvokeModel (Nova). Required (non-empty) when subtitle_llm_keywords=true."
}

resource "aws_iam_role_policy" "job_subtitle_bedrock" {
  count = var.subtitle_llm_keywords && length(var.subtitle_model_arns) > 0 ? 1 : 0
  name  = "${var.name}-job-subtitle-bedrock"
  role  = aws_iam_role.job.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "InvokeSubtitleKeywords"
      Effect   = "Allow"
      Action   = ["bedrock:InvokeModel"]
      Resource = var.subtitle_model_arns
    }]
  })
}
