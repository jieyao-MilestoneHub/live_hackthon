# render-workflow: the Artifact Render data plane (demand.md §十一).
#
#   PlanCreative (Lambda: subtitle/effects/render_spec, →QUEUED)
#     → SubmitRender (AWS Batch submitJob.sync: real FFmpeg → ARTIFACT_READY)
#     → Succeed        (Catch → MarkRenderFailed → Fail)
#
# The Batch container (workers.render) runs render_worker.run with the real
# FFmpegEncoder; PlanCreative + MarkRenderFailed are backend-image Lambdas.

locals {
  lambdas = {
    plan_creative      = { handler = "workers.lambda_handlers.plan_creative", timeout = 120, memory = 512 }
    mark_render_failed = { handler = "workers.lambda_handlers.mark_render_failed", timeout = 30, memory = 256 }
  }
  lambda_env = {
    USE_INMEMORY   = "0"
    ENV            = var.env
    DYNAMODB_TABLE = var.dynamodb_table
    WORK_BUCKET    = var.work_bucket
    OUTPUT_BUCKET  = var.output_bucket
  }
}

# --- Shared Lambda role (plan writes plans to work bucket; both touch Dynamo) --
data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${var.name}-plan"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "lambda" {
  statement {
    sid       = "Dynamo"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query"]
    resources = [var.table_arn, "${var.table_arn}/index/*"]
  }
  statement {
    sid       = "WorkBucket"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${var.bucket_arns["work"]}/*"]
  }
}

resource "aws_iam_role_policy" "lambda" {
  name   = "${var.name}-plan-policy"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda.json
}

resource "aws_lambda_function" "fn" {
  for_each = local.lambdas

  function_name = "${var.name}-${replace(each.key, "_", "-")}"
  role          = aws_iam_role.lambda.arn
  package_type  = "Image"
  image_uri     = var.image_uri
  memory_size   = each.value.memory
  timeout       = each.value.timeout
  architectures = ["x86_64"]

  image_config {
    command = [each.value.handler]
  }
  environment {
    variables = local.lambda_env
  }
  tags = merge(var.tags, { Purpose = "render-plan" })
}

# --- State machine ---------------------------------------------------------
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
    sid       = "InvokeLambdas"
    actions   = ["lambda:InvokeFunction"]
    resources = [for fn in aws_lambda_function.fn : fn.arn]
  }
  # Batch .sync: SubmitJob + poll + the managed EventBridge rule it creates.
  statement {
    sid       = "Batch"
    actions   = ["batch:SubmitJob", "batch:DescribeJobs", "batch:TerminateJob"]
    resources = ["*"]
  }
  statement {
    sid       = "BatchSyncEvents"
    actions   = ["events:PutTargets", "events:PutRule", "events:DescribeRule"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "sfn" {
  name   = "${var.name}-sfn-policy"
  role   = aws_iam_role.sfn.id
  policy = data.aws_iam_policy_document.sfn.json
}

locals {
  definition = jsonencode({
    Comment = "Video Artifact Render Workflow (demand.md §十一)."
    StartAt = "PlanCreative"
    States = {
      PlanCreative = {
        Type       = "Task"
        Resource   = "arn:aws:states:::lambda:invoke"
        Parameters = { FunctionName = aws_lambda_function.fn["plan_creative"].arn, "Payload.$" = "$" }
        ResultPath = null
        Retry = [{
          ErrorEquals     = ["Lambda.ServiceException", "Lambda.TooManyRequestsException", "States.TaskFailed"]
          IntervalSeconds = 5
          MaxAttempts     = 2
          BackoffRate     = 2.0
        }]
        Catch = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "MarkRenderFailed" }]
        Next  = "SubmitRender"
      }
      SubmitRender = {
        Type     = "Task"
        Resource = "arn:aws:states:::batch:submitJob.sync"
        Parameters = {
          "JobName.$"   = "$.render_id"
          JobDefinition = var.batch_job_definition_arn
          JobQueue      = var.batch_job_queue_arn
          ContainerOverrides = {
            Environment = [
              { Name = "PROJECT_ID", "Value.$" = "$.project_id" },
              { Name = "RENDER_ID", "Value.$" = "$.render_id" },
            ]
          }
        }
        ResultPath = null
        Catch      = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "MarkRenderFailed" }]
        Next       = "RenderSucceeded"
      }
      RenderSucceeded = { Type = "Succeed" }
      MarkRenderFailed = {
        Type       = "Task"
        Resource   = "arn:aws:states:::lambda:invoke"
        Parameters = { FunctionName = aws_lambda_function.fn["mark_render_failed"].arn, "Payload.$" = "$" }
        ResultPath = null
        Next       = "RenderFailed"
      }
      RenderFailed = {
        Type  = "Fail"
        Error = "RenderPipelineFailed"
        Cause = "See MarkRenderFailed / render error_message."
      }
    }
  })
}

resource "aws_sfn_state_machine" "render" {
  name       = "${var.name}-workflow"
  role_arn   = aws_iam_role.sfn.arn
  type       = "STANDARD"
  definition = local.definition
  tags       = var.tags
}

output "state_machine_arn" {
  value = aws_sfn_state_machine.render.arn
}
