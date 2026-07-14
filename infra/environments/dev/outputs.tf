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

# --- Storage (video-editor S3 buckets, §16) ---
output "raw_bucket" {
  description = "Raw upload bucket name (video-editor-raw)."
  value       = module.storage_editor.raw_bucket
}

output "work_bucket" {
  description = "Intermediate work bucket name (transcript/analysis/timelines/renders)."
  value       = module.storage_editor.work_bucket
}

output "output_bucket" {
  description = "Output bucket name (artifacts)."
  value       = module.storage_editor.output_bucket
}

# --- State (DynamoDB VideoEditor single table, §17) ---
output "dynamodb_table_name" {
  description = "VideoEditor DynamoDB single-table name."
  value       = module.state_table.table_name
}

# --- Auth (Cognito, §3/§4) ---
output "cognito_user_pool_id" {
  description = "Cognito user pool id (backend verifies JWTs against this pool)."
  value       = module.auth.user_pool_id
}

output "cognito_user_pool_endpoint" {
  description = "User pool endpoint; JWT issuer = https://<endpoint>."
  value       = module.auth.user_pool_endpoint
}

output "cognito_user_pool_client_id" {
  description = "Public web app client id used by the frontend for login."
  value       = module.auth.user_pool_client_id
}
