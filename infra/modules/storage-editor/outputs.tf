output "raw_bucket" {
  description = "Raw upload bucket name (video-editor-raw). Source uploads land here."
  value       = aws_s3_bucket.this["raw"].id
}

output "work_bucket" {
  description = "Intermediate work bucket name (transcript/analysis/timelines/renders)."
  value       = aws_s3_bucket.this["work"].id
}

output "output_bucket" {
  description = "Output bucket name (artifacts: final/preview/thumbnail/manifest)."
  value       = aws_s3_bucket.this["output"].id
}

output "bucket_arns" {
  description = "Map of logical name (raw/work/output) -> bucket ARN, for IAM policies."
  value       = { for k, b in aws_s3_bucket.this : k => b.arn }
}
