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

# CloudWatch Logs only. Attach S3 (raw/work/output) + DynamoDB access here
# when the backend starts calling AWS APIs (M1).
resource "aws_iam_role_policy_attachment" "basic" {
  role       = aws_iam_role.exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_lambda_function" "backend" {
  function_name = var.name
  role          = aws_iam_role.exec.arn
  package_type  = "Image"
  image_uri     = var.image_uri
  memory_size   = var.memory
  timeout       = var.timeout
  architectures = ["x86_64"]

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
