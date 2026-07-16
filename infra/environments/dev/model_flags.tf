# No-stub model flags (2026-07-16): flip the remaining stub model paths to real
# Bedrock. edit_planner_llm / highlight_llm_enrich / moderation_enabled already
# exist in variables.tf; these add narration + subtitle-keyword wiring.

variable "progress_narrator_llm" {
  type        = bool
  default     = false
  description = "AI progress narration (#39) via Bedrock Haiku. OFF -> StubNarrator. Reuses bedrock_model_arns for IAM (Haiku is the edit-planner fast model)."
}

variable "progress_narrator_model_id" {
  type        = string
  default     = ""
  description = "Narration model id (e.g. us.anthropic.claude-haiku-4-5-20251001-v1:0). Empty -> app default."
}

variable "subtitle_llm_keywords" {
  type        = bool
  default     = false
  description = "LLM-picked subtitle keyword animation (Bedrock Nova) in the render job. OFF -> deterministic rules."
}

variable "subtitle_model_arns" {
  type        = list(string)
  default     = []
  description = "Nova foundation-model / inference-profile ARNs the render job may InvokeModel for subtitle keywords."
}
