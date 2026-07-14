output "raw_bucket" {
  description = "Name of the raw upload bucket (video-raw)."
  value       = aws_s3_bucket.this["raw"].id
}

output "work_bucket" {
  description = "Name of the intermediate work bucket (transcripts/analysis)."
  value       = aws_s3_bucket.this["work"].id
}

output "output_bucket" {
  description = "Name of the output bucket (clips/manifests)."
  value       = aws_s3_bucket.this["output"].id
}

output "bucket_arns" {
  description = "Map of logical name -> bucket ARN."
  value       = { for k, b in aws_s3_bucket.this : k => b.arn }
}

output "dynamodb_table_name" {
  description = "VideoJobs single-table name."
  value       = aws_dynamodb_table.video_jobs.name
}

output "dynamodb_table_arn" {
  description = "VideoJobs table ARN."
  value       = aws_dynamodb_table.video_jobs.arn
}
