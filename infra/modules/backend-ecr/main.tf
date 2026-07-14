# backend-ecr: ECR repository for the backend (FastAPI) container image.
# The Lambda backend (modules/backend-lambda) pulls its image from this repo
# (tag :lambda). Two App Runner IAM roles are kept but unused — App Runner is
# SCP-blocked in this workshop account (see the NOTE at the bottom); reinstate a
# service only if this ever runs in an account without that SCP.

locals {
  # A public.ecr.aws image needs image_repository_type=ECR_PUBLIC and no
  # access role; a private ECR image needs type=ECR + an access role.
  # This lets `terraform validate` and a first `apply` work with the nginx
  # placeholder, then switch cleanly to the real private image via var override.
  is_public_image       = startswith(var.backend_image, "public.ecr.aws")
  image_repository_type = local.is_public_image ? "ECR_PUBLIC" : "ECR"
}

resource "aws_ecr_repository" "backend" {
  name                 = var.name
  image_tag_mutability = "MUTABLE"
  force_delete         = true # dev convenience — allows destroy with images present

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = merge(var.tags, { Purpose = "backend-image" })
}

# --- IAM: ECR access role (App Runner service principal pulls the image) ---
data "aws_iam_policy_document" "apprunner_build_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["build.apprunner.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "apprunner_ecr_access" {
  name               = "${var.name}-apprunner-ecr-access"
  assume_role_policy = data.aws_iam_policy_document.apprunner_build_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "apprunner_ecr_access" {
  role       = aws_iam_role.apprunner_ecr_access.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
}

# --- IAM: instance (task) role, assumed by the running container ---
# Currently has no attached policies. Attach S3 (raw/work/output) + DynamoDB
# access here when the backend starts calling AWS APIs.
data "aws_iam_policy_document" "apprunner_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["tasks.apprunner.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "apprunner_instance" {
  name               = "${var.name}-apprunner-instance"
  assume_role_policy = data.aws_iam_policy_document.apprunner_tasks_assume.json
  tags               = var.tags
}

# NOTE: aws_apprunner_service is intentionally REMOVED — the WSParticipantRole
# SCP denies apprunner:CreateService (and CreateAutoScalingConfiguration) in this
# workshop account. The backend is deployed as a Lambda + Function URL instead
# (see modules/backend-lambda). This module now only manages the ECR repo (which
# holds both the :latest App Runner image and the :lambda image) plus the two
# App Runner IAM roles (kept, unused, harmless — reinstate the service if this
# repo ever runs in an account without the SCP).
