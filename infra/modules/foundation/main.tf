# Foundation: raw/work/output S3 buckets + VideoJobs DynamoDB single table.
# Mirrors docs/aws-infra.md §5 (S3 key schema / lifecycle) and §6 (DynamoDB).

# Account id suffix keeps the globally-unique S3 bucket names from colliding
# with other accounts (the project prefix alone is not guaranteed unique).
data "aws_caller_identity" "current" {}

locals {
  # Logical buckets per §5. Prefixed with the project name + account id for
  # global uniqueness (S3 bucket names are global) while keeping video-* schema.
  suffix = data.aws_caller_identity.current.account_id
  buckets = {
    raw    = "${var.project}-video-raw-${var.env}-${local.suffix}"
    work   = "${var.project}-video-work-${var.env}-${local.suffix}"
    output = "${var.project}-video-output-${var.env}-${local.suffix}"
  }

  # §5 lifecycle stubs — minimal, tune per data-governance needs.
  # null = no auto-expiry yet (output clips are governed by the user's plan).
  lifecycle_expiration_days = {
    raw    = 90   # 原始影片 30–90 天
    work   = 14   # Work 中間檔 7–14 天
    output = null # Output clips 依使用者方案決定
  }
}

resource "aws_s3_bucket" "this" {
  for_each = local.buckets
  bucket   = each.value
  tags     = merge(var.tags, { Purpose = "video-${each.key}" })
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each                = aws_s3_bucket.this
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  versioning_configuration {
    status = "Enabled"
  }
}

# SSE. §5 recommends SSE-KMS; SSE-S3 (AES256) is used here to keep the
# walking-skeleton free of a KMS key + key policy. Swap to aws:kms when the
# pipeline needs cross-service grants.
resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id

  # Abort incomplete multipart uploads after 1 day (§5).
  rule {
    id     = "abort-incomplete-multipart"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  # Optional current-version expiration for transient buckets.
  dynamic "rule" {
    for_each = local.lifecycle_expiration_days[each.key] == null ? [] : [local.lifecycle_expiration_days[each.key]]
    content {
      id     = "expire-current"
      status = "Enabled"
      filter {}
      expiration {
        days = rule.value
      }
    }
  }
}

# VideoJobs single-table design (§6): PK/SK plus GSI1 (tenant listing) and
# GSI2 (ops/status queries). TTL on expires_at. PAY_PER_REQUEST for dev.
resource "aws_dynamodb_table" "video_jobs" {
  name         = "VideoJobs-${var.env}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }
  attribute {
    name = "SK"
    type = "S"
  }
  attribute {
    name = "GSI1PK"
    type = "S"
  }
  attribute {
    name = "GSI1SK"
    type = "S"
  }
  attribute {
    name = "GSI2PK"
    type = "S"
  }
  attribute {
    name = "GSI2SK"
    type = "S"
  }

  # GSI1: list jobs by tenant — GET /jobs?tenant_id=... (§6).
  global_secondary_index {
    name            = "GSI1"
    hash_key        = "GSI1PK"
    range_key       = "GSI1SK"
    projection_type = "ALL"
  }

  # GSI2: ops queries by status (failed / stuck jobs, reconciliation) (§6).
  global_secondary_index {
    name            = "GSI2"
    hash_key        = "GSI2PK"
    range_key       = "GSI2SK"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = merge(var.tags, { Purpose = "video-jobs-state" })
}
