output "table_name" {
  description = "VideoEditor single-table name."
  value       = aws_dynamodb_table.video_editor.name
}

output "table_arn" {
  description = "VideoEditor table ARN (for backend/worker IAM policies)."
  value       = aws_dynamodb_table.video_editor.arn
}
