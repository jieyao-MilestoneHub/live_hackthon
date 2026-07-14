variable "env" {
  type        = string
  description = "Deployment environment (dev/staging/prod). Suffixes the table name."
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "Extra tags merged onto the table (on top of the provider default_tags)."
}
