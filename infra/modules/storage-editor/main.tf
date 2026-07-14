# storage-editor: the three video-editor S3 buckets (raw / work / output).
# Authoritative layout: docs/demand.md §16 (S3 儲存配置). Replaces the legacy
# foundation video-raw/work/output buckets (M0 job model) with the M1 Project
# model. Input and output live in SEPARATE buckets so output objects never
# re-trigger the analysis pipeline (event-loop avoidance).
#
# §16 key layout (documented here; prefix/suffix event filtering is wired later
# in M2 EventBridge — M1 only creates + hardens the buckets):
#
#   video-editor-raw-{env}/
#     tenant={tenant_id}/project={project_id}/
#       source/source.mp4            <- ObjectCreated here starts analysis (M2)
#       upload/metadata.json
#
#   video-editor-work-{env}/
#     tenant={tenant_id}/project={project_id}/
#       transcript/{raw.json,transcript.v1.json}
#       analysis/highlights.v1.json
#       timelines/version={n}/timeline.json          (append-only versions)
#       renders/render={render_id}/
#         {subtitle.vtt,subtitle-style.json,effect-plan.json,render-spec.json,ffmpeg.log}
#
#   video-editor-output-{env}/
#     tenant={tenant_id}/project={project_id}/
#       artifacts/artifact={artifact_id}/
#         {final.mp4,preview.mp4,thumbnail.jpg,subtitle.vtt,manifest.json}

# Account id suffix keeps the globally-unique S3 bucket names from colliding
# with other accounts (the project prefix alone is not guaranteed unique).
data "aws_caller_identity" "current" {}

locals {
  suffix = data.aws_caller_identity.current.account_id

  # Logical buckets per §16. Prefixed with project + account id for global
  # uniqueness while keeping the video-editor-{role} schema from demand.md.
  buckets = {
    raw    = "${var.project}-video-editor-raw-${var.env}-${local.suffix}"
    work   = "${var.project}-video-editor-work-${var.env}-${local.suffix}"
    output = "${var.project}-video-editor-output-${var.env}-${local.suffix}"
  }

  # Lifecycle stubs — minimal, tune per data-governance needs.
  # null = no auto-expiry yet (output artifacts are governed by the user's plan).
  lifecycle_expiration_days = {
    raw    = 90   # 原始影片 30–90 天
    work   = 14   # Work 中間檔（transcript/analysis/timelines/renders）7–14 天
    output = null # Output artifacts 依使用者方案決定
  }
}

resource "aws_s3_bucket" "this" {
  for_each = local.buckets
  bucket   = each.value
  tags     = merge(var.tags, { Purpose = "video-editor-${each.key}" })
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

# SSE. §16 hardening baseline. SSE-S3 (AES256) keeps the walking-skeleton free
# of a KMS key + key policy; swap to aws:kms when the pipeline needs
# cross-service grants (Transcribe / Batch reading raw+work).
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

  # Abort incomplete multipart uploads after 1 day (§16). Direct multipart
  # upload to the raw bucket can leave dangling parts if the browser drops.
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
