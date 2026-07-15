output "function_name" {
  value       = aws_lambda_function.consumer.function_name
  description = "ai-task-render consumer Lambda name."
}

output "function_arn" {
  value = aws_lambda_function.consumer.arn
}

output "role_arn" {
  value = aws_iam_role.consumer.arn
}
