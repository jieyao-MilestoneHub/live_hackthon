output "user_pool_id" {
  description = "Cognito user pool id (e.g. us-east-1_xxxxxxxxx)."
  value       = aws_cognito_user_pool.editor.id
}

output "user_pool_arn" {
  description = "Cognito user pool ARN."
  value       = aws_cognito_user_pool.editor.arn
}

output "user_pool_endpoint" {
  description = "User pool endpoint (cognito-idp.<region>.amazonaws.com/<pool_id>). JWT issuer = https://<this>; JWKS = https://<this>/.well-known/jwks.json."
  value       = aws_cognito_user_pool.editor.endpoint
}

output "user_pool_client_id" {
  description = "Public web app client id used by the frontend for login."
  value       = aws_cognito_user_pool_client.web.id
}
