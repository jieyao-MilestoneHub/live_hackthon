# render-batch: AWS Batch on Fargate for the heavy FFmpeg render (demand.md §十三).
#
# SCP RISK (probe FIRST): Batch on Fargate launches ECS tasks + needs VPC
# networking. If the workshop SCP blocks Batch/ECS/Fargate, fall back per the
# plan (Batch EC2, then ffmpeg-in-Lambda for short clips). Networking uses the
# default VPC's subnets with a public IP (assignPublicIp=ENABLED) so the task can
# pull the ECR image without a NAT gateway.

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

resource "aws_security_group" "render" {
  name        = "${var.name}-batch"
  description = "Egress-only SG for the FFmpeg Batch task (ECR/S3/DynamoDB)."
  vpc_id      = data.aws_vpc.default.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = var.tags
}

# --- ECS task execution role (ECR pull + CloudWatch Logs) ------------------
data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.name}-exec"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# --- Job role (the container's own AWS permissions) ------------------------
resource "aws_iam_role" "job" {
  name               = "${var.name}-job"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "job" {
  statement {
    sid       = "Dynamo"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query"]
    resources = [var.table_arn, "${var.table_arn}/index/*"]
  }
  statement {
    sid       = "S3ReadSourceAndPlans"
    actions   = ["s3:GetObject"]
    resources = ["${var.bucket_arns["raw"]}/*", "${var.bucket_arns["work"]}/*"]
  }
  statement {
    sid       = "S3WriteArtifacts"
    actions   = ["s3:PutObject"]
    resources = ["${var.bucket_arns["output"]}/*", "${var.bucket_arns["work"]}/*"]
  }
}

resource "aws_iam_role_policy" "job" {
  name   = "${var.name}-job-policy"
  role   = aws_iam_role.job.id
  policy = data.aws_iam_policy_document.job.json
}

# --- Compute environment (Fargate) -----------------------------------------
# service_role omitted: Batch uses the AWSServiceRoleForBatch service-linked
# role (auto-created). If apply errors, create it once with
#   aws iam create-service-linked-role --aws-service-name batch.amazonaws.com
resource "aws_batch_compute_environment" "render" {
  compute_environment_name = "${var.name}-ce"
  type                     = "MANAGED"
  state                    = "ENABLED"

  compute_resources {
    type               = "FARGATE"
    max_vcpus          = var.max_vcpus
    subnets            = data.aws_subnets.default.ids
    security_group_ids = [aws_security_group.render.id]
  }
  tags = var.tags
}

resource "aws_batch_job_queue" "render" {
  name                 = "${var.name}-queue"
  state                = "ENABLED"
  priority             = 1
  compute_environments = [aws_batch_compute_environment.render.arn]
  tags                 = var.tags
}

resource "aws_batch_job_definition" "render" {
  name                  = "${var.name}-job"
  type                  = "container"
  platform_capabilities = ["FARGATE"]

  container_properties = jsonencode({
    image            = var.image_uri
    executionRoleArn = aws_iam_role.execution.arn
    jobRoleArn       = aws_iam_role.job.arn
    resourceRequirements = [
      { type = "VCPU", value = var.job_vcpu },
      { type = "MEMORY", value = var.job_memory },
    ]
    ephemeralStorage             = { sizeInGiB = var.ephemeral_storage_gib }
    networkConfiguration         = { assignPublicIp = "ENABLED" }
    fargatePlatformConfiguration = { platformVersion = "LATEST" }
    # PROJECT_ID / RENDER_ID are injected per-job by the render Step Functions
    # via ContainerOverrides. These are the fixed runtime settings.
    environment = [
      { name = "USE_INMEMORY", value = "0" },
      { name = "RENDER_ENCODER", value = "ffmpeg" },
      { name = "ENV", value = var.env },
      { name = "DYNAMODB_TABLE", value = var.dynamodb_table },
      { name = "RAW_BUCKET", value = var.raw_bucket },
      { name = "WORK_BUCKET", value = var.work_bucket },
      { name = "OUTPUT_BUCKET", value = var.output_bucket },
    ]
    logConfiguration = { logDriver = "awslogs" }
  })
  tags = var.tags
}

output "job_queue_arn" {
  value = aws_batch_job_queue.render.arn
}
output "job_definition_arn" {
  value = aws_batch_job_definition.render.arn
}
