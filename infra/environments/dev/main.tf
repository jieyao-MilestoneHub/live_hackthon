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

# Backend runtime: Lambda container behind API Gateway HTTP API (App Runner
# alternative). M2.0: wired to the real DynamoDB table + S3 buckets with
# USE_INMEMORY=0 so the control plane actually persists.
module "backend_lambda" {
  source    = "../../modules/backend-lambda"
  name      = "${var.project}-backend-${var.env}"
  image_uri = var.backend_lambda_image

  env            = var.env
  use_inmemory   = false
  dynamodb_table = module.state_table.table_name
  table_arn      = module.state_table.table_arn
  raw_bucket     = module.storage_editor.raw_bucket
  work_bucket    = module.storage_editor.work_bucket
  output_bucket  = module.storage_editor.output_bucket
  bucket_arns    = module.storage_editor.bucket_arns

  # When set, POST /renders StartExecutions the render workflow (async) instead
  # of running Creative Planning inline, and grants states:StartExecution.
  render_state_machine_arn = module.render_workflow.state_machine_arn
  enable_render_start      = true
}

# --- Analysis plane (M2.1): S3 event → SQS → Starter → Step Functions ------
# Worker Lambdas (one backend image, per-handler CMD) + the Standard state
# machine that drives a real upload to READY_TO_EDIT (demand.md §六/§七/§八).
module "analysis_workflow" {
  source     = "../../modules/analysis-workflow"
  name       = "${var.project}-analysis-${var.env}"
  image_uri  = var.backend_lambda_image
  env        = var.env
  region     = var.region
  account_id = data.aws_caller_identity.current.account_id

  dynamodb_table = module.state_table.table_name
  table_arn      = module.state_table.table_arn
  raw_bucket     = module.storage_editor.raw_bucket
  work_bucket    = module.storage_editor.work_bucket
  output_bucket  = module.storage_editor.output_bucket
  bucket_arns    = module.storage_editor.bucket_arns
}

module "analysis_ingress" {
  source            = "../../modules/analysis-ingress"
  name              = "${var.project}-analysis-${var.env}"
  image_uri         = var.backend_lambda_image
  env               = var.env
  raw_bucket        = module.storage_editor.raw_bucket
  dynamodb_table    = module.state_table.table_name
  table_arn         = module.state_table.table_arn
  state_machine_arn = module.analysis_workflow.state_machine_arn
}

# --- Render plane (M2.2): POST /renders → StartExecution → Batch FFmpeg -----
# SCP RISK: probe AWS Batch (Fargate) before apply — see render-batch/main.tf.
module "render_ecr" {
  source = "../../modules/render-ecr"
  name   = "${var.project}-render-${var.env}"
}

module "render_batch" {
  source    = "../../modules/render-batch"
  name      = "${var.project}-render-${var.env}"
  image_uri = var.render_image
  env       = var.env

  dynamodb_table = module.state_table.table_name
  table_arn      = module.state_table.table_arn
  raw_bucket     = module.storage_editor.raw_bucket
  work_bucket    = module.storage_editor.work_bucket
  output_bucket  = module.storage_editor.output_bucket
  bucket_arns    = module.storage_editor.bucket_arns
}

module "render_workflow" {
  source    = "../../modules/render-workflow"
  name      = "${var.project}-render-${var.env}"
  image_uri = var.backend_lambda_image
  env       = var.env

  dynamodb_table = module.state_table.table_name
  table_arn      = module.state_table.table_arn
  work_bucket    = module.storage_editor.work_bucket
  output_bucket  = module.storage_editor.output_bucket
  bucket_arns    = module.storage_editor.bucket_arns

  batch_job_queue_arn      = module.render_batch.job_queue_arn
  batch_job_definition_arn = module.render_batch.job_definition_arn
}
