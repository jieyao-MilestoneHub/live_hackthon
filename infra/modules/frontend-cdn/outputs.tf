output "bucket_name" {
  description = "Private S3 bucket holding the static frontend export."
  value       = aws_s3_bucket.frontend.id
}

output "bucket_arn" {
  description = "ARN of the frontend bucket."
  value       = aws_s3_bucket.frontend.arn
}

output "cloudfront_domain" {
  description = "CloudFront distribution domain name (e.g. dxxxx.cloudfront.net)."
  value       = aws_cloudfront_distribution.frontend.domain_name
}

output "distribution_id" {
  description = "CloudFront distribution id (for cache invalidation)."
  value       = aws_cloudfront_distribution.frontend.id
}
