variable "bucket_name" {
  type        = string
  description = "Name of the private S3 bucket that stores the static Next.js export."
}

variable "default_root_object" {
  type        = string
  default     = "index.html"
  description = "CloudFront default root object."
}

variable "price_class" {
  type        = string
  default     = "PriceClass_200"
  description = "CloudFront price class (200 covers NA/EU/Asia)."
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "Extra tags merged onto every resource."
}
