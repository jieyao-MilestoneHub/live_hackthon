# analysis-workflow: the Analysis & Composition data plane (demand.md §七/§八).
#
# One backend container image, N worker Lambdas (image_config.command picks the
# handler in workers/lambda_handlers.py). A Standard Step Functions state machine
# chains them: ValidateSource → Probe → Transcribe → DetectHighlights →
# Compose → MarkReadyToEdit, with Catch → MarkFailed. Only pointers cross states
# (ResultPath: null keeps the original {project_id,…} flowing); big docs live in
# S3 / DynamoDB (SFN payload limit is 256 KB).

locals {
  # Per-worker sizing. transcribe blocks on the real Transcribe job (poll loop),
  # so it gets the 15-min max timeout + more memory.
  workers = {
    validate_source   = { timeout = 60, memory = 256 }
    probe_metadata    = { timeout = 60, memory = 256 }
    transcribe        = { timeout = 900, memory = 1024 }
    detect_highlights = { timeout = 300, memory = 512 }
    compose_timeline  = { timeout = 120, memory = 512 }
    mark_ready        = { timeout = 30, memory = 256 }
    mark_failed       = { timeout = 30, memory = 256 }
  }

  worker_env = {
    USE_INMEMORY         = "0"
    ENV                  = var.env
    DYNAMODB_TABLE       = var.dynamodb_table
    RAW_BUCKET           = var.raw_bucket
    WORK_BUCKET          = var.work_bucket
    OUTPUT_BUCKET        = var.output_bucket
    HIGHLIGHT_LLM_ENRICH = var.highlight_llm_enrich ? "1" : "0"
  }
}

# --- Shared worker IAM role (plane-level least-privilege) -------------------
data "aws_iam_policy_document" "worker_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "worker" {
  name               = "${var.name}-worker"
  assume_role_policy = data.aws_iam_policy_document.worker_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "worker_logs" {
  role       = aws_iam_role.worker.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "worker" {
  statement {
    sid = "DynamoVideoEditor"
    actions = [
      "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
      "dynamodb:Query", "dynamodb:BatchWriteItem", "dynamodb:BatchGetItem",
    ]
    resources = [var.table_arn, "${var.table_arn}/index/*"]
  }

  # Read source (raw), read+write intermediate docs (work). Transcribe writes
  # its batch output to the work bucket as OutputBucketName.
  statement {
    sid       = "S3RawRead"
    actions   = ["s3:GetObject"]
    resources = ["${var.bucket_arns["raw"]}/*"]
  }
  statement {
    sid       = "S3Work"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${var.bucket_arns["work"]}/*"]
  }

  # Amazon Transcribe has no resource-level permissions.
  statement {
    sid       = "Transcribe"
    actions   = ["transcribe:StartTranscriptionJob", "transcribe:GetTranscriptionJob"]
    resources = ["*"]
  }

  # Bedrock highlight enrichment (detect_highlights) — Nova review model only.
  statement {
    sid       = "BedrockInvoke"
    actions   = ["bedrock:InvokeModel"]
    resources = ["arn:aws:bedrock:${var.region}::foundation-model/${var.bedrock_review_model_id}"]
  }
}

resource "aws_iam_role_policy" "worker" {
  name   = "${var.name}-worker-data"
  role   = aws_iam_role.worker.id
  policy = data.aws_iam_policy_document.worker.json
}

# --- Worker Lambdas (same image, per-handler CMD) --------------------------
resource "aws_lambda_function" "worker" {
  for_each = local.workers

  function_name = "${var.name}-${replace(each.key, "_", "-")}"
  role          = aws_iam_role.worker.arn
  package_type  = "Image"
  image_uri     = var.image_uri
  memory_size   = each.value.memory
  timeout       = each.value.timeout
  architectures = ["x86_64"]

  image_config {
    command = ["workers.lambda_handlers.${each.key}"]
  }

  environment {
    variables = local.worker_env
  }

  tags = merge(var.tags, { Purpose = "analysis-worker" })
}

# --- Step Functions state machine (Standard) -------------------------------
data "aws_iam_policy_document" "sfn_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sfn" {
  name               = "${var.name}-sfn"
  assume_role_policy = data.aws_iam_policy_document.sfn_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "sfn" {
  statement {
    sid       = "InvokeWorkers"
    actions   = ["lambda:InvokeFunction"]
    resources = [for fn in aws_lambda_function.worker : fn.arn]
  }
}

resource "aws_iam_role_policy" "sfn" {
  name   = "${var.name}-sfn-invoke"
  role   = aws_iam_role.sfn.id
  policy = data.aws_iam_policy_document.sfn.json
}

locals {
  # A happy-path Task: invoke the worker, discard its result (ResultPath: null)
  # so the original {project_id,…} keeps flowing; retry transient failures;
  # catch everything to MarkFailed (preserving input under $.error).
  retry = [{
    ErrorEquals     = ["States.TaskFailed", "Lambda.ServiceException", "Lambda.TooManyRequestsException", "Lambda.Unknown"]
    IntervalSeconds = 5
    MaxAttempts     = 2
    BackoffRate     = 2.0
  }]
  catch = [{
    ErrorEquals = ["States.ALL"]
    ResultPath  = "$.error"
    Next        = "MarkFailed"
  }]

  definition = jsonencode({
    Comment = "Video Analysis and Composition Workflow (demand.md §七). Ends at READY_TO_EDIT; never waits for the user."
    StartAt = "ValidateSource"
    States = {
      ValidateSource = {
        Type       = "Task"
        Resource   = "arn:aws:states:::lambda:invoke"
        Parameters = { FunctionName = aws_lambda_function.worker["validate_source"].arn, "Payload.$" = "$" }
        ResultPath = null
        Retry      = local.retry
        Catch      = local.catch
        Next       = "ProbeVideoMetadata"
      }
      ProbeVideoMetadata = {
        Type       = "Task"
        Resource   = "arn:aws:states:::lambda:invoke"
        Parameters = { FunctionName = aws_lambda_function.worker["probe_metadata"].arn, "Payload.$" = "$" }
        ResultPath = null
        Retry      = local.retry
        Catch      = local.catch
        Next       = "StartTranscription"
      }
      StartTranscription = {
        Type       = "Task"
        Resource   = "arn:aws:states:::lambda:invoke"
        Parameters = { FunctionName = aws_lambda_function.worker["transcribe"].arn, "Payload.$" = "$" }
        ResultPath = null
        Retry      = local.retry
        Catch      = local.catch
        Next       = "DetectHighlights"
      }
      DetectHighlights = {
        Type       = "Task"
        Resource   = "arn:aws:states:::lambda:invoke"
        Parameters = { FunctionName = aws_lambda_function.worker["detect_highlights"].arn, "Payload.$" = "$" }
        ResultPath = null
        Retry      = local.retry
        Catch      = local.catch
        Next       = "ComposeInitialTimeline"
      }
      ComposeInitialTimeline = {
        Type       = "Task"
        Resource   = "arn:aws:states:::lambda:invoke"
        Parameters = { FunctionName = aws_lambda_function.worker["compose_timeline"].arn, "Payload.$" = "$" }
        ResultPath = null
        Retry      = local.retry
        Catch      = local.catch
        Next       = "MarkReadyToEdit"
      }
      MarkReadyToEdit = {
        Type       = "Task"
        Resource   = "arn:aws:states:::lambda:invoke"
        Parameters = { FunctionName = aws_lambda_function.worker["mark_ready"].arn, "Payload.$" = "$" }
        ResultPath = null
        End        = true
      }
      MarkFailed = {
        Type       = "Task"
        Resource   = "arn:aws:states:::lambda:invoke"
        Parameters = { FunctionName = aws_lambda_function.worker["mark_failed"].arn, "Payload.$" = "$" }
        ResultPath = null
        Next       = "PipelineFailed"
      }
      PipelineFailed = {
        Type  = "Fail"
        Error = "AnalysisPipelineFailed"
        Cause = "See the MarkFailed step / project error_message."
      }
    }
  })
}

resource "aws_sfn_state_machine" "analysis" {
  name       = "${var.name}-workflow"
  role_arn   = aws_iam_role.sfn.arn
  type       = "STANDARD"
  definition = local.definition
  tags       = var.tags
}
