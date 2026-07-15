output "state_machine_arn" {
  description = "Analysis & Composition state machine ARN (Starter Lambda StartExecution target)."
  value       = aws_sfn_state_machine.analysis.arn
}

output "worker_function_arns" {
  description = "Map handler name -> Lambda ARN, for observability / wiring."
  value       = { for k, fn in aws_lambda_function.worker : k => fn.arn }
}

output "worker_function_names" {
  description = "List of worker Lambda function names (for CloudWatch dashboards/alarms)."
  value       = [for fn in aws_lambda_function.worker : fn.function_name]
}

output "worker_role_arn" {
  description = "Shared analysis-worker execution role ARN."
  value       = aws_iam_role.worker.arn
}
