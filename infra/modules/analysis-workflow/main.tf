# analysis-workflow: the Analysis & Composition data plane (demand.md §七/§八).
#
# One backend container image, N worker Lambdas (image_config.command picks the
# handler in workers/lambda_handlers.py). A Standard Step Functions state machine
# chains them: ValidateSource → Probe → Transcribe → DetectHighlights →
# Compose → MarkReadyToEdit, with Catch → MarkFailed. Only pointers cross states
# (ResultPath: null keeps the original {project_id,…} flowing); big docs live in
# S3 / DynamoDB (SFN payload limit is 256 KB).

locals {
  # Per-worker sizing (timeout s, memory MB, ephemeral /tmp MB).
  # poll_transcription is NON-blocking (start the job, then Step Functions Waits
  # + polls). transcribe is heavier: for sources over Amazon Transcribe's 2GB
  # limit it stream-copies the S3 source into ≤1.8GB segments via ffmpeg before
  # firing per-segment jobs, so it needs a big /tmp + a long timeout.
  workers = {
    validate_source     = { timeout = 60, memory = 256, ephemeral = 512 }
    probe_metadata      = { timeout = 60, memory = 256, ephemeral = 512 }
    start_moderation    = { timeout = 60, memory = 256, ephemeral = 512 }
    transcribe          = { timeout = 900, memory = 2048, ephemeral = 10240 }
    poll_transcription  = { timeout = 60, memory = 512, ephemeral = 512 }
    detect_highlights   = { timeout = 300, memory = 512, ephemeral = 512 }
    moderation_decision = { timeout = 120, memory = 512, ephemeral = 512 }
    compose_timeline    = { timeout = 120, memory = 512, ephemeral = 512 }
    mark_ready          = { timeout = 30, memory = 256, ephemeral = 512 }
    mark_blocked        = { timeout = 30, memory = 256, ephemeral = 512 }
    mark_failed         = { timeout = 30, memory = 256, ephemeral = 512 }
  }

  worker_env = {
    USE_INMEMORY         = "0"
    ENV                  = var.env
    DYNAMODB_TABLE       = var.dynamodb_table
    RAW_BUCKET           = var.raw_bucket
    WORK_BUCKET          = var.work_bucket
    OUTPUT_BUCKET        = var.output_bucket
    HIGHLIGHT_LLM_ENRICH = var.highlight_llm_enrich ? "1" : "0"
    MODERATION_ENABLED   = var.moderation_enabled ? "1" : "0"
    # Auto dual-track: mark_ready StartExecutions the render SFN for pipeline + edit.
    RENDER_STATE_MACHINE_ARN      = var.render_state_machine_arn
    EDIT_PLANNER_LLM              = var.edit_planner_llm ? "1" : "0"
    EDIT_PLANNER_MODEL_ID         = var.edit_planner_model_id
    EDIT_PLANNER_QUALITY_MODEL_ID = var.edit_planner_quality_model_id
    # AI progress narration (#39): step() calls the narrator synchronously inside
    # these workers; OFF → StubNarrator template. ON needs the narrator bedrock
    # grant below (reuses bedrock_model_arns, which already holds the Haiku model).
    PROGRESS_NARRATOR_LLM      = var.progress_narrator_llm ? "1" : "0"
    PROGRESS_NARRATOR_MODEL_ID = var.progress_narrator_model_id
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

  # Rekognition video content moderation (start_moderation / moderation_decision).
  # No resource-level permissions on these APIs.
  statement {
    sid       = "RekognitionModeration"
    actions   = ["rekognition:StartContentModeration", "rekognition:GetContentModeration"]
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

# Auto dual-track (WS3): mark_ready StartExecutions the render SFN for pipeline +
# edit. Only granted when the render workflow ARN is wired.
resource "aws_iam_role_policy" "worker_start_render" {
  count = var.render_state_machine_arn == "" ? 0 : 1
  name  = "${var.name}-worker-start-render"
  role  = aws_iam_role.worker.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "StartRender"
      Effect   = "Allow"
      Action   = "states:StartExecution"
      Resource = var.render_state_machine_arn
    }]
  })
}

# Edit route NL planner on Claude (gated): only when edit_planner_llm + ARNs set.
# Default off → the deterministic Stub planner runs, so no Bedrock grant exists.
resource "aws_iam_role_policy" "worker_edit_bedrock" {
  count = var.edit_planner_llm && length(var.bedrock_model_arns) > 0 ? 1 : 0
  name  = "${var.name}-worker-edit-bedrock"
  role  = aws_iam_role.worker.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "InvokeClaude"
      Effect   = "Allow"
      Action   = ["bedrock:InvokeModel"]
      Resource = var.bedrock_model_arns
    }]
  })
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

  ephemeral_storage {
    size = each.value.ephemeral
  }

  # -1 = unreserved (default). Async transcribe means workers no longer block, so
  # peak concurrency is low; this lever exists only if a low-cap account needs the
  # pipeline pool protected. See var.worker_reserved_concurrency.
  reserved_concurrent_executions = var.worker_reserved_concurrency

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
        Next       = "StartModeration"
      }
      # Kick the async Rekognition visual scan now so it overlaps transcription
      # (no added wall-clock); the verdict is made later at ModerationDecision.
      StartModeration = {
        Type       = "Task"
        Resource   = "arn:aws:states:::lambda:invoke"
        Parameters = { FunctionName = aws_lambda_function.worker["start_moderation"].arn, "Payload.$" = "$" }
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
      # Async transcription: START the job (returns immediately), then Wait/Poll
      # in the state machine until COMPLETED/FAILED. This removes the old ~10-min
      # in-Lambda poll cap (long videos now transcribe fully) and frees the
      # concurrency slot the blocking Lambda used to hold for the whole job.
      StartTranscription = {
        Type       = "Task"
        Resource   = "arn:aws:states:::lambda:invoke"
        Parameters = { FunctionName = aws_lambda_function.worker["transcribe"].arn, "Payload.$" = "$" }
        ResultPath = null
        Retry      = local.retry
        Catch      = local.catch
        Next       = "WaitForTranscription"
      }
      WaitForTranscription = {
        Type    = "Wait"
        Seconds = var.transcribe_poll_wait_sec
        Next    = "GetTranscription"
      }
      GetTranscription = {
        Type       = "Task"
        Resource   = "arn:aws:states:::lambda:invoke"
        Parameters = { FunctionName = aws_lambda_function.worker["poll_transcription"].arn, "Payload.$" = "$" }
        ResultPath = "$.transcription"
        Retry      = local.retry
        Catch      = local.catch
        Next       = "TranscriptionComplete"
      }
      TranscriptionComplete = {
        Type = "Choice"
        Choices = [
          {
            Variable     = "$.transcription.Payload.status"
            StringEquals = "COMPLETED"
            Next         = "DetectHighlights"
          },
          {
            Variable     = "$.transcription.Payload.status"
            StringEquals = "FAILED"
            Next         = "MarkFailed"
          },
        ]
        Default = "WaitForTranscription"
      }
      DetectHighlights = {
        Type       = "Task"
        Resource   = "arn:aws:states:::lambda:invoke"
        Parameters = { FunctionName = aws_lambda_function.worker["detect_highlights"].arn, "Payload.$" = "$" }
        ResultPath = null
        Retry      = local.retry
        Catch      = local.catch
        Next       = "ModerationDecision"
      }
      # Content-moderation gate: poll the visual scan + run the zh-TW text scan
      # over transcript + AI-generated highlight copy → tiered verdict. Runs after
      # DetectHighlights so the AI titles/reasons (which get burned into subtitles)
      # are available to scan.
      ModerationDecision = {
        Type       = "Task"
        Resource   = "arn:aws:states:::lambda:invoke"
        Parameters = { FunctionName = aws_lambda_function.worker["moderation_decision"].arn, "Payload.$" = "$" }
        ResultPath = "$.moderation"
        Retry      = local.retry
        Catch      = local.catch
        Next       = "ModerationGate"
      }
      ModerationGate = {
        Type = "Choice"
        Choices = [
          {
            Variable     = "$.moderation.Payload.status"
            StringEquals = "PENDING"
            Next         = "WaitForModeration"
          },
          {
            Variable     = "$.moderation.Payload.status"
            StringEquals = "BLOCKED"
            Next         = "MarkBlocked"
          },
        ]
        # ALLOWED / FLAGGED both continue (FLAGGED is editable but publish is
        # gated at the render/download API until a moderator override).
        Default = "ComposeInitialTimeline"
      }
      WaitForModeration = {
        Type    = "Wait"
        Seconds = var.transcribe_poll_wait_sec
        Next    = "ModerationDecision"
      }
      MarkBlocked = {
        Type       = "Task"
        Resource   = "arn:aws:states:::lambda:invoke"
        Parameters = { FunctionName = aws_lambda_function.worker["mark_blocked"].arn, "Payload.$" = "$" }
        ResultPath = null
        End        = true
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
