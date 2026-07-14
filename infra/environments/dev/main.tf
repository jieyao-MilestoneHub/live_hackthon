# dev environment — wires the three walking-skeleton modules.
# Resource names follow ${project}-<role>-${env}. Global Project/Env/ManagedBy
# tags come from the provider default_tags in providers.tf.

# Foundation: raw/work/output buckets + VideoJobs DynamoDB table (§5, §6).
module "foundation" {
  source  = "../../modules/foundation"
  project = var.project
  env     = var.env
}

# Frontend: private S3 + CloudFront (OAC) for the Next.js static export.
module "frontend" {
  source      = "../../modules/frontend-cdn"
  bucket_name = "${var.project}-frontend-${var.env}"
}

# Backend: ECR repo + App Runner service for the FastAPI container.
module "backend" {
  source        = "../../modules/backend-apprunner"
  name          = "${var.project}-backend-${var.env}"
  backend_image = var.backend_image
}
