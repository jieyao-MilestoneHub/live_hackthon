output "intake_queue_url" {
  value       = aws_sqs_queue.intake.id
  description = "analysis-intake queue URL."
}

output "intake_queue_arn" {
  value = aws_sqs_queue.intake.arn
}

output "starter_function_arn" {
  value = aws_lambda_function.starter.arn
}

# DLQ names for CloudWatch depth alarms (observability). A message here means a
# record failed maxReceiveCount times — nobody consumes the DLQ, so an alarm is
# the only signal it happened (WS4).
output "intake_dlq_name" {
  value = aws_sqs_queue.intake_dlq.name
}

output "chat_intake_dlq_name" {
  value = aws_sqs_queue.chat_intake_dlq.name
}
