output "ecr_repository_url" {
  description = "ECR repository URL to push the backend image to."
  value       = aws_ecr_repository.backend.repository_url
}

output "ecr_repository_arn" {
  description = "ECR repository ARN."
  value       = aws_ecr_repository.backend.arn
}
