# analysis-ingress: the event entry for the analysis plane (demand.md §六/§十九).
#
#   S3 raw source/ ObjectCreated → EventBridge → SQS analysis-intake (+DLQ)
#     → Starter Lambda (idempotent) → StartExecution(analysis workflow)
#
# Also provisions the ai-task queue (+DLQ) from §十九 for future light AI work
# (e.g. async re-compose); no consumer is wired yet (the control plane runs the
# pure Composer inline for now).

locals {
  raw_bucket_arn = "arn:aws:s3:::${var.raw_bucket}"
}

# --- SQS: analysis-intake (+DLQ) -------------------------------------------
resource "aws_sqs_queue" "intake_dlq" {
  name                      = "${var.name}-analysis-intake-dlq"
  message_retention_seconds = 1209600 # 14 days
  tags                      = var.tags
}

resource "aws_sqs_queue" "intake" {
  name                       = "${var.name}-analysis-intake"
  visibility_timeout_seconds = 90 # >= Starter Lambda timeout
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.intake_dlq.arn
    maxReceiveCount     = 5
  })
  tags = var.tags
}

# Allow EventBridge to deliver S3 events into the intake queue.
data "aws_iam_policy_document" "intake_policy" {
  statement {
    sid       = "AllowEventBridge"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.intake.arn]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.source_created.arn]
    }
  }
}

resource "aws_sqs_queue_policy" "intake" {
  queue_url = aws_sqs_queue.intake.id
  policy    = data.aws_iam_policy_document.intake_policy.json
}

# --- SQS: ai-task (+DLQ) — §十九 -------------------------------------------
# Consumer = the ai-task-render Lambda (edit-by-language ffmpeg encode, module
# ai-task-render). visibility_timeout MUST be >= that Lambda's timeout (900) or
# SQS redelivers mid-encode → duplicate work (the consumer is idempotent, but a
# redelivery still wastes an invocation).
resource "aws_sqs_queue" "ai_task_dlq" {
  name                      = "${var.name}-ai-task-dlq"
  message_retention_seconds = 1209600
  tags                      = var.tags
}

resource "aws_sqs_queue" "ai_task" {
  name                       = "${var.name}-ai-task"
  visibility_timeout_seconds = 960 # >= ai-task-render Lambda timeout (900)
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ai_task_dlq.arn
    maxReceiveCount     = 5
  })
  tags = var.tags
}

# --- EventBridge: S3 raw source/ ObjectCreated → intake queue --------------
# Enable S3 → EventBridge for the raw bucket (only one notification per bucket;
# storage-editor sets none).
resource "aws_s3_bucket_notification" "raw_eventbridge" {
  bucket      = var.raw_bucket
  eventbridge = true
}

resource "aws_cloudwatch_event_rule" "source_created" {
  name        = "${var.name}-source-created"
  description = "Route raw source uploads to the analysis intake queue."
  event_pattern = jsonencode({
    source        = ["aws.s3"]
    "detail-type" = ["Object Created"]
    detail = {
      bucket = { name = [var.raw_bucket] }
      object = { key = [{ suffix = "source/source.mp4" }] }
    }
  })
  tags = var.tags
}

resource "aws_cloudwatch_event_target" "to_intake" {
  rule = aws_cloudwatch_event_rule.source_created.name
  arn  = aws_sqs_queue.intake.arn
}

# --- Starter Lambda (idempotent StartExecution) ----------------------------
data "aws_iam_policy_document" "starter_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "starter" {
  name               = "${var.name}-starter"
  assume_role_policy = data.aws_iam_policy_document.starter_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "starter_logs" {
  role       = aws_iam_role.starter.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "starter" {
  statement {
    sid       = "StartAnalysis"
    actions   = ["states:StartExecution"]
    resources = [var.state_machine_arn]
  }
  statement {
    sid = "ConsumeIntake"
    actions = [
      "sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes",
    ]
    resources = [aws_sqs_queue.intake.arn]
  }
  # Read the project's analysis_source so chat-LOG projects skip auto-Transcribe.
  statement {
    sid       = "ReadProjectForAnalysisSourceGate"
    actions   = ["dynamodb:GetItem"]
    resources = [var.table_arn]
  }
}

resource "aws_iam_role_policy" "starter" {
  name   = "${var.name}-starter-policy"
  role   = aws_iam_role.starter.id
  policy = data.aws_iam_policy_document.starter.json
}

resource "aws_lambda_function" "starter" {
  function_name = "${var.name}-starter"
  role          = aws_iam_role.starter.arn
  package_type  = "Image"
  image_uri     = var.image_uri
  memory_size   = 256
  timeout       = 60
  architectures = ["x86_64"]

  image_config {
    command = ["workers.lambda_handlers.starter"]
  }

  environment {
    variables = {
      # Starter parses the S3 key + StartExecutions the analysis workflow, and
      # reads the project's analysis_source (DynamoDB GetItem) to skip auto
      # Transcribe for chat-LOG projects. No raw-bucket access needed.
      USE_INMEMORY               = "0"
      ENV                        = var.env
      ANALYSIS_STATE_MACHINE_ARN = var.state_machine_arn
      DYNAMODB_TABLE             = var.dynamodb_table
    }
  }

  tags = merge(var.tags, { Purpose = "analysis-starter" })
}

resource "aws_lambda_event_source_mapping" "starter" {
  event_source_arn = aws_sqs_queue.intake.arn
  function_name    = aws_lambda_function.starter.arn
  batch_size       = 10
  enabled          = true

  # Partial-batch responses: only the records the handler reports (returns in
  # batchItemFailures) are re-driven, not the whole batch of 10. Requires
  # starter() to return {"batchItemFailures": [...]}.
  function_response_types = ["ReportBatchItemFailures"]
}

# ===========================================================================
# Chat-LOG ingress: a bare chat.csv drop auto-runs the full chat pipeline.
#
#   S3 raw source/chat.csv ObjectCreated → EventBridge → SQS chat-intake (+DLQ)
#     → chat_starter Lambda (auto-create → analyze → compose → StartExecution render)
#
# Distinct from the transcribe path above (suffix source/source.mp4) so the two
# never cross-fire on the same object.
# ===========================================================================

resource "aws_sqs_queue" "chat_intake_dlq" {
  name                      = "${var.name}-chat-intake-dlq"
  message_retention_seconds = 1209600
  tags                      = var.tags
}

resource "aws_sqs_queue" "chat_intake" {
  name                       = "${var.name}-chat-intake"
  visibility_timeout_seconds = 360 # >= chat_starter Lambda timeout (300)
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.chat_intake_dlq.arn
    maxReceiveCount     = 3
  })
  tags = var.tags
}

data "aws_iam_policy_document" "chat_intake_policy" {
  statement {
    sid       = "AllowEventBridge"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.chat_intake.arn]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.chat_created.arn]
    }
  }
}

resource "aws_sqs_queue_policy" "chat_intake" {
  queue_url = aws_sqs_queue.chat_intake.id
  policy    = data.aws_iam_policy_document.chat_intake_policy.json
}

resource "aws_cloudwatch_event_rule" "chat_created" {
  name        = "${var.name}-chat-created"
  description = "Route raw chat.csv uploads to the chat intake queue."
  event_pattern = jsonencode({
    source        = ["aws.s3"]
    "detail-type" = ["Object Created"]
    detail = {
      bucket = { name = [var.raw_bucket] }
      object = { key = [{ suffix = "source/chat.csv" }] }
    }
  })
  tags = var.tags
}

resource "aws_cloudwatch_event_target" "to_chat_intake" {
  rule = aws_cloudwatch_event_rule.chat_created.name
  arn  = aws_sqs_queue.chat_intake.arn
}

# --- chat_starter Lambda (full pipeline: analyze → compose → StartExecution render)
resource "aws_iam_role" "chat_starter" {
  name               = "${var.name}-chat-starter"
  assume_role_policy = data.aws_iam_policy_document.starter_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "chat_starter_logs" {
  role       = aws_iam_role.chat_starter.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "chat_starter" {
  statement {
    sid       = "StartRender"
    actions   = ["states:StartExecution"]
    resources = [var.render_state_machine_arn]
  }
  statement {
    sid       = "ConsumeChatIntake"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [aws_sqs_queue.chat_intake.arn]
  }
  statement {
    sid       = "ReadRaw"
    actions   = ["s3:GetObject"]
    resources = ["${local.raw_bucket_arn}/*"]
  }
  statement {
    sid       = "ReadWriteWork"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["arn:aws:s3:::${var.work_bucket}/*"]
  }
  statement {
    sid = "ProjectTable"
    actions = [
      "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
      "dynamodb:Query", "dynamodb:BatchWriteItem",
    ]
    resources = [var.table_arn, "${var.table_arn}/index/*"]
  }
}

resource "aws_iam_role_policy" "chat_starter" {
  name   = "${var.name}-chat-starter-policy"
  role   = aws_iam_role.chat_starter.id
  policy = data.aws_iam_policy_document.chat_starter.json
}

resource "aws_lambda_function" "chat_starter" {
  function_name = "${var.name}-chat-starter"
  role          = aws_iam_role.chat_starter.arn
  package_type  = "Image"
  image_uri     = var.image_uri
  memory_size   = 1024 # inline CSV parse + rule-based analysis
  timeout       = 300
  architectures = ["x86_64"]

  image_config {
    command = ["workers.lambda_handlers.chat_starter"]
  }

  environment {
    variables = {
      USE_INMEMORY             = "0"
      ENV                      = var.env
      DYNAMODB_TABLE           = var.dynamodb_table
      RAW_BUCKET               = var.raw_bucket
      WORK_BUCKET              = var.work_bucket
      OUTPUT_BUCKET            = var.output_bucket
      RENDER_STATE_MACHINE_ARN = var.render_state_machine_arn
      HIGHLIGHT_LLM_ENRICH     = "0"
      CHAT_TARGET_DURATION_MS  = "30000"
    }
  }

  tags = merge(var.tags, { Purpose = "chat-starter" })
}

resource "aws_lambda_event_source_mapping" "chat_starter" {
  event_source_arn = aws_sqs_queue.chat_intake.arn
  function_name    = aws_lambda_function.chat_starter.arn
  batch_size       = 1
  enabled          = true
}
