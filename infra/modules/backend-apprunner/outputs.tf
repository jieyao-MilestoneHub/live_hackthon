output "service_url" {
  description = "Public HTTPS URL of the App Runner service."
  value       = "https://${aws_apprunner_service.backend.service_url}"
}

output "service_arn" {
  description = "App Runner service ARN."
  value       = aws_apprunner_service.backend.arn
}

output "ecr_repository_url" {
  description = "ECR repository URL to push the backend image to."
  value       = aws_ecr_repository.backend.repository_url
}

output "ecr_repository_arn" {
  description = "ECR repository ARN."
  value       = aws_ecr_repository.backend.arn
}
