# Backend as a Lambda container image, exposed via an API Gateway HTTP API.
# Why not App Runner: SCP denies apprunner:CreateService in the workshop account.
# Why not a Lambda Function URL: the workshop guardrail 403s anonymous Function
# URL invokes. API Gateway HTTP API is allowed and gives a public execute-api URL.

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "exec" {
  name               = "${var.name}-lambda-exec"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

# CloudWatch Logs.
resource "aws_iam_role_policy_attachment" "basic" {
  role       = aws_iam_role.exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Data-plane access the control plane needs once USE_INMEMORY=0 (M2.0):
# VideoEditor single table + S3 raw (presign/multipart) / work / output.
# Least-privilege: object-level on each bucket, no s3:* / no cross-table.
data "aws_iam_policy_document" "backend_data" {
  statement {
    sid    = "DynamoVideoEditor"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:Query",
      "dynamodb:BatchWriteItem",
      "dynamodb:BatchGetItem",
    ]
    resources = [var.table_arn, "${var.table_arn}/index/*"]
  }

  # Raw bucket: create_multipart_upload + per-part presign + abort.
  statement {
    sid    = "S3RawObjects"
    effect = "Allow"
    actions = [
      "s3:PutObject",
      "s3:GetObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = ["${var.bucket_arns["raw"]}/*"]
  }

  statement {
    sid       = "S3RawBucket"
    effect    = "Allow"
    actions   = ["s3:ListBucketMultipartUploads"]
    resources = [var.bucket_arns["raw"]]
  }

  # Work bucket: creative planning writes plans; workers read them.
  statement {
    sid       = "S3WorkObjects"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${var.bucket_arns["work"]}/*"]
  }

  # Output bucket: presigned artifact download.
  statement {
    sid       = "S3OutputObjects"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${var.bucket_arns["output"]}/*"]
  }
}

resource "aws_iam_role_policy" "backend_data" {
  name   = "${var.name}-data-access"
  role   = aws_iam_role.exec.id
  policy = data.aws_iam_policy_document.backend_data.json
}

# Only when the render workflow is deployed: let POST /renders StartExecution it.
resource "aws_iam_role_policy" "backend_render" {
  count = var.enable_render_start ? 1 : 0
  name  = "${var.name}-start-render"
  role  = aws_iam_role.exec.id
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

resource "aws_lambda_function" "backend" {
  function_name = var.name
  role          = aws_iam_role.exec.arn
  package_type  = "Image"
  image_uri     = var.image_uri
  memory_size   = var.memory
  timeout       = var.timeout
  architectures = ["x86_64"]

  # -1 = unreserved. Set ≥30 for the batch demo so upload control-plane calls
  # (create-session / complete) can't be starved by pipeline Lambdas sharing the
  # account concurrency pool.
  reserved_concurrent_executions = var.reserved_concurrency

  # Runtime config for app/settings.py. AWS_REGION is reserved/injected by the
  # Lambda runtime, so we must NOT set it here. Names must match the REAL
  # resources (…-dev-<acct>) — the app defaults (VideoEditor / video-editor-*)
  # would silently miss.
  environment {
    variables = {
      USE_INMEMORY             = var.use_inmemory ? "1" : "0"
      ENV                      = var.env
      DYNAMODB_TABLE           = var.dynamodb_table
      RAW_BUCKET               = var.raw_bucket
      WORK_BUCKET              = var.work_bucket
      OUTPUT_BUCKET            = var.output_bucket
      PRESIGN_EXPIRY_SEC       = tostring(var.presign_expiry_sec)
      MAX_UPLOAD_BYTES         = tostring(var.max_upload_bytes)
      MAX_BATCH_FILES          = tostring(var.max_batch_files)
      MODERATION_ENABLED       = var.moderation_enabled ? "1" : "0"
      RENDER_STATE_MACHINE_ARN = var.render_state_machine_arn
    }
  }

  tags = merge(var.tags, { Purpose = "backend-service" })
}

# --- API Gateway HTTP API → Lambda proxy ($default route catches every path
#     and method, including OPTIONS; CORS is handled by FastAPI's middleware
#     so we do NOT set api-level CORS to avoid duplicate headers). ---
resource "aws_apigatewayv2_api" "http" {
  name          = var.name
  protocol_type = "HTTP"
  tags          = merge(var.tags, { Purpose = "backend-api" })
}

resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.backend.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "default" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.backend.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}
