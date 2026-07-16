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

  # Batch upload: 6h presign + 10GB cap + 1024MB come from module defaults.
  # reserved_concurrency stays unreserved unless the demo knob is set (see var).
  reserved_concurrency = var.backend_reserved_concurrency
  moderation_enabled   = var.moderation_enabled

  # Cognito JWT authorizer on the API (blocks anonymous callers from the pipeline).
  cognito_client_id = module.auth.user_pool_client_id
  cognito_issuer    = module.auth.user_pool_endpoint

  # When set, POST /renders StartExecutions the render workflow (async) instead
  # of running Creative Planning inline, and grants states:StartExecution.
  render_state_machine_arn = module.render_workflow.state_machine_arn
  enable_render_start      = true

  # edit-by-language: the edit route renders through the render SFN → Batch (same
  # data flow as pipeline; StartExecution already granted by enable_render_start).
  # EDIT_PLANNER_LLM picks Claude-on-Bedrock vs the deterministic Stub planner.
  edit_planner_llm              = var.edit_planner_llm
  edit_planner_model_id         = var.edit_planner_model_id
  edit_planner_quality_model_id = var.edit_planner_quality_model_id
  bedrock_model_arns            = var.bedrock_model_arns
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

  # Demo knob: set false to drop ~150 concurrent Bedrock converse calls off the
  # critical path (deterministic scorer still produces highlights).
  highlight_llm_enrich = var.highlight_llm_enrich
  moderation_enabled   = var.moderation_enabled

  # Auto dual-track: mark_ready StartExecutions the render SFN for pipeline + edit
  # (needs states:StartExecution + the ARN). The edit route's NL planner is Stub by
  # default; edit_planner_llm=true switches it to Claude on Bedrock.
  render_state_machine_arn      = module.render_workflow.state_machine_arn
  edit_planner_llm              = var.edit_planner_llm
  edit_planner_model_id         = var.edit_planner_model_id
  edit_planner_quality_model_id = var.edit_planner_quality_model_id
  bedrock_model_arns            = var.bedrock_model_arns

  # AI progress narration (#39): narrator runs synchronously in these workers.
  progress_narrator_llm      = var.progress_narrator_llm
  progress_narrator_model_id = var.progress_narrator_model_id
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

  # Chat-LOG ingress: chat_starter runs the full pipeline and StartExecutions render.
  work_bucket              = module.storage_editor.work_bucket
  output_bucket            = module.storage_editor.output_bucket
  render_state_machine_arn = module.render_workflow.state_machine_arn

  # Chat-path model flags: highlight enrichment (Nova, already granted) + progress
  # narration (Haiku, reuses bedrock_model_arns for the scoped grant).
  highlight_llm_enrich       = var.highlight_llm_enrich
  progress_narrator_llm      = var.progress_narrator_llm
  progress_narrator_model_id = var.progress_narrator_model_id
  bedrock_model_arns         = var.bedrock_model_arns
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

  # of a long 1080p source needs CPU + headroom). 50 GiB ephemeral holds a 10GB
  # streamed source.mp4 + temp segments. max_vcpus scales concurrent renders for
  # the batch demo (job_vcpu=2 → max_vcpus/2 concurrent).
  job_vcpu              = "2"
  job_memory            = "8192"
  max_vcpus             = var.render_max_vcpus
  ephemeral_storage_gib = 50

  dynamodb_table = module.state_table.table_name
  table_arn      = module.state_table.table_arn
  raw_bucket     = module.storage_editor.raw_bucket
  work_bucket    = module.storage_editor.work_bucket
  output_bucket  = module.storage_editor.output_bucket
  bucket_arns    = module.storage_editor.bucket_arns

  # Subtitle keyword animation (Bedrock Nova) in the FFmpeg render job.
  subtitle_llm_keywords = var.subtitle_llm_keywords
  subtitle_model_arns   = var.subtitle_model_arns
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

# (Removed: the ai-task ffmpeg-in-Lambda encoder. The edit route now renders through
# the SAME render SFN → Batch as pipeline — one encoder, one data flow. See WS3.)

# --- Observability: CloudWatch dashboard + alarms to prove the batch demo is
#     healthy (Lambda throttles/concurrency, SFN, DynamoDB, SQS backlog). ------
module "observability" {
  source = "../../modules/observability"
  name   = "${var.project}-${var.env}"
  region = var.region

  backend_function_name      = module.backend_lambda.function_name
  worker_function_names      = module.analysis_workflow.worker_function_names
  analysis_state_machine_arn = module.analysis_workflow.state_machine_arn
  table_name                 = module.state_table.table_name
  # analysis-intake queue name = last ':' segment of its ARN.
  intake_queue_name = element(split(":", module.analysis_ingress.intake_queue_arn), 5)

  # DLQ depth alarms → email (WS4). Nobody consumes the DLQs, so the alarm is the
  # only signal a record was dropped after all retries.
  alert_email = var.alert_email
  dlq_queue_names = [
    module.analysis_ingress.intake_dlq_name,
    module.analysis_ingress.chat_intake_dlq_name,
  ]
}
