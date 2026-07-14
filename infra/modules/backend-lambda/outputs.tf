output "api_endpoint" {
  description = "Public base URL of the backend (API Gateway HTTP API → Lambda)."
  value       = aws_apigatewayv2_api.http.api_endpoint
}

output "function_name" {
  description = "Backend Lambda function name."
  value       = aws_lambda_function.backend.function_name
}
