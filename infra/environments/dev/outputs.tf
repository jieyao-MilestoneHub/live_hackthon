# --- Frontend (S3 + CloudFront) ---
output "cloudfront_domain" {
  description = "CloudFront domain serving the frontend."
  value       = module.frontend.cloudfront_domain
}

output "frontend_bucket" {
  description = "Private S3 bucket for the static frontend export."
  value       = module.frontend.bucket_name
}

output "cloudfront_distribution_id" {
  description = "CloudFront distribution id (used by deploy.sh cache invalidation)."
  value       = module.frontend.distribution_id
}

# --- Backend (ECR + Lambda behind API Gateway HTTP API) ---
output "backend_api_endpoint" {
  description = "Public base URL of the backend (API Gateway → Lambda)."
  value       = module.backend_lambda.api_endpoint
}

output "ecr_repository_url" {
  description = "ECR repo URL to push the backend image to."
  value       = module.backend.ecr_repository_url
}

# --- Foundation (S3 + DynamoDB) ---
output "raw_bucket" {
  description = "Raw upload bucket name."
  value       = module.foundation.raw_bucket
}

output "work_bucket" {
  description = "Intermediate work bucket name."
  value       = module.foundation.work_bucket
}

output "output_bucket" {
  description = "Output (clips/manifests) bucket name."
  value       = module.foundation.output_bucket
}

output "dynamodb_table_name" {
  description = "VideoJobs DynamoDB table name."
  value       = module.foundation.dynamodb_table_name
}
