# dev environment — wires the three walking-skeleton modules.
# Resource names follow ${project}-<role>-${env}. Global Project/Env/ManagedBy
# tags come from the provider default_tags in providers.tf.

# Account id → globally-unique S3 bucket name suffix.
data "aws_caller_identity" "current" {}

# Storage: video-editor raw/work/output buckets (demand.md §16).
module "storage_editor" {
  source  = "../../modules/storage-editor"
  project = var.project
  env     = var.env
}

# State: VideoEditor DynamoDB single table (demand.md §17).
module "state_table" {
  source = "../../modules/state-table"
  env    = var.env
}

# Auth: Cognito user pool + public web client for the editor (demand.md §3/§4).
module "auth" {
  source  = "../../modules/auth"
  project = var.project
  env     = var.env
}

# Frontend: private S3 + CloudFront (OAC) for the Next.js static export.
module "frontend" {
  source      = "../../modules/frontend-cdn"
  bucket_name = "${var.project}-frontend-${var.env}-${data.aws_caller_identity.current.account_id}"
}

# Backend ECR repo (App Runner service removed — SCP-blocked in workshop account).
module "backend" {
  source        = "../../modules/backend-ecr"
  name          = "${var.project}-backend-${var.env}"
  backend_image = var.backend_image
}

# Backend runtime: Lambda container + public Function URL (App Runner alternative).
module "backend_lambda" {
  source    = "../../modules/backend-lambda"
  name      = "${var.project}-backend-${var.env}"
  image_uri = var.backend_lambda_image
}
