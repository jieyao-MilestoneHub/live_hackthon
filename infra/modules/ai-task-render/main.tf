# ai-task-render: the ffmpeg-in-Lambda consumer for edit-by-language encode.
#
#   sidecar (POST /edit-by-language) → enqueue_ai_task → SQS ai-task
#     → THIS Lambda (workers.lambda_handlers.ai_task_render) → render_worker.run
#       (RENDER_ENCODER=ffmpeg) → artifact
#
# Reuses the ai-task queue already provisioned (consumer-less) by analysis-ingress.
# Does NOT touch any Step Functions / Batch. The plan (effects.v1 / subtitle.v1)
# is written to the work bucket by the sidecar before the message is enqueued, so
# this Lambda only needs S3 (raw read / work read / output write) + DynamoDB — NO
# Bedrock (the LLM planning ran synchronously in the control-plane Lambda).

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "consumer" {
  name               = "${var.name}-ai-task-render"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "logs" {
  role       = aws_iam_role.consumer.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Least-privilege data plane (mirrors render-batch's job role): stream the source
# from raw, read the plans from work, write the artifact to output, drive the
# Render/Project items on the table. No Bedrock.
data "aws_iam_policy_document" "data" {
  statement {
    sid       = "ConsumeAiTask"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [var.ai_task_queue_arn]
  }
  statement {
    sid       = "ReadRaw"
    actions   = ["s3:GetObject"]
    resources = ["${var.bucket_arns["raw"]}/*"]
  }
  statement {
    sid       = "ReadWriteWork"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${var.bucket_arns["work"]}/*"]
  }
  statement {
    sid       = "WriteOutput"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${var.bucket_arns["output"]}/*"]
  }
  statement {
    sid = "RenderTable"
    actions = [
      "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
      "dynamodb:Query", "dynamodb:BatchWriteItem",
    ]
    resources = [var.table_arn, "${var.table_arn}/index/*"]
  }
}

resource "aws_iam_role_policy" "data" {
  name   = "${var.name}-ai-task-render-data"
  role   = aws_iam_role.consumer.id
  policy = data.aws_iam_policy_document.data.json
}

resource "aws_lambda_function" "consumer" {
  function_name = "${var.name}-ai-task-render"
  role          = aws_iam_role.consumer.arn
  package_type  = "Image"
  image_uri     = var.image_uri
  memory_size   = var.memory  # 10240 ≈ 6 vCPU for the encode
  timeout       = var.timeout # ≤60s output encodes in well under this
  architectures = ["x86_64"]

  # Protect the account concurrency limit (high-memory functions draw on the
  # >3008 MB quota, L-B99A9384) and cap S3/DynamoDB fan-out under bursty load.
  reserved_concurrent_executions = var.reserved_concurrency

  ephemeral_storage {
    size = var.ephemeral_mb # holds the streamed multi-GB source.mp4 + temp segments
  }

  image_config {
    command = ["workers.lambda_handlers.ai_task_render"]
  }

  environment {
    variables = {
      USE_INMEMORY          = "0"
      ENV                   = var.env
      DYNAMODB_TABLE        = var.dynamodb_table
      RAW_BUCKET            = var.raw_bucket
      WORK_BUCKET           = var.work_bucket
      OUTPUT_BUCKET         = var.output_bucket
      RENDER_ENCODER        = "ffmpeg"
      RENDER_REQUIRE_FFMPEG = "1" # fail-closed: never publish a stub as a real artifact
      FFMPEG_BINARY         = var.ffmpeg_binary
      SUBTITLE_FONTS_DIR    = var.subtitle_fonts_dir
      SUBTITLE_FONT         = var.subtitle_font
    }
  }

  tags = merge(var.tags, { Purpose = "ai-task-render" })
}

# SQS → Lambda. batch_size 1: one render per invocation (each is a full encode).
# The ai-task queue's visibility_timeout (set in analysis-ingress) must be >= this
# function's timeout, or SQS redelivers mid-encode.
resource "aws_lambda_event_source_mapping" "ai_task" {
  event_source_arn = var.ai_task_queue_arn
  function_name    = aws_lambda_function.consumer.arn
  batch_size       = 1
  enabled          = true
}
