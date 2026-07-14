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

# --- SQS: ai-task (+DLQ) — §十九 (no consumer yet) -------------------------
resource "aws_sqs_queue" "ai_task_dlq" {
  name                      = "${var.name}-ai-task-dlq"
  message_retention_seconds = 1209600
  tags                      = var.tags
}

resource "aws_sqs_queue" "ai_task" {
  name                       = "${var.name}-ai-task"
  visibility_timeout_seconds = 300
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
      # The starter only parses the S3 key and calls StartExecution; it never
      # touches DynamoDB or the raw bucket, so no table/bucket env is needed.
      USE_INMEMORY               = "0"
      ENV                        = var.env
      ANALYSIS_STATE_MACHINE_ARN = var.state_machine_arn
    }
  }

  tags = merge(var.tags, { Purpose = "analysis-starter" })
}

resource "aws_lambda_event_source_mapping" "starter" {
  event_source_arn = aws_sqs_queue.intake.arn
  function_name    = aws_lambda_function.starter.arn
  batch_size       = 10
  enabled          = true
}
