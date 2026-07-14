output "intake_queue_url" {
  value       = aws_sqs_queue.intake.id
  description = "analysis-intake queue URL."
}

output "intake_queue_arn" {
  value = aws_sqs_queue.intake.arn
}

output "ai_task_queue_url" {
  value       = aws_sqs_queue.ai_task.id
  description = "ai-task queue URL (§十九; no consumer yet)."
}

output "ai_task_queue_arn" {
  value = aws_sqs_queue.ai_task.arn
}

output "starter_function_arn" {
  value = aws_lambda_function.starter.arn
}
