# state-table: the VideoEditor DynamoDB single table.
# Authoritative schema: docs/demand.md §17 (DynamoDB Table Schema). Replaces the
# legacy foundation VideoJobs table (M0 job model) with the M1 Project model.
#
# DynamoDB is schemaless for non-key attributes, so Terraform declares ONLY the
# key attributes (PK/SK + GSI keys). The item bodies below are written by the
# backend (contract owner) — listed here for reference, NOT defined in TF:
#
#   Project    PK=PROJECT#{project_id}  SK=META
#     tenant_id, user_id, title, status, target_duration_ms, source_bucket,
#     source_key, source_version_id, source_duration_ms, latest_timeline_version,
#     latest_render_id, latest_artifact_id, created_at, updated_at, version
#   Highlight  PK=PROJECT#{project_id}  SK=HIGHLIGHT#{highlight_id}
#     start_ms, end_ms, score, reason, transcript, suggested_title, selected, locked
#   Timeline   PK=PROJECT#{project_id}  SK=TIMELINE#VERSION#{version}   (append-only)
#     target_duration_ms, actual_duration_ms, clips, subtitle_settings,
#     effect_settings, aspect_ratio, created_by, created_at
#   Render     PK=PROJECT#{project_id}  SK=RENDER#{render_id}
#     timeline_version, status, current_stage, effect_seed, batch_job_id,
#     render_spec_key, artifact_id, error_code, error_message, created_at,
#     started_at, completed_at
#   Artifact   PK=PROJECT#{project_id}  SK=ARTIFACT#{artifact_id}
#     render_id, video_key, preview_key, thumbnail_key, manifest_key,
#     duration_ms, size_bytes, checksum, created_at
#
# Access pattern per project: Query PK=PROJECT#{id} + SK begins_with(...) fetches
# the project META and all its highlights/timelines/renders/artifacts.

resource "aws_dynamodb_table" "video_editor" {
  name         = "VideoEditor-${var.env}"
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

  # GSI1 — id-only entity lookup. NOTE: demand.md §17 does not define any GSI,
  # but the §4 Editor API exposes `GET /renders/{render_id}` and
  # `GET /artifacts/{artifact_id}/download` whose paths carry no project_id, so
  # the base table (PK=PROJECT#{id}) cannot resolve them. The backend populates
  # GSI1PK on those items (e.g. RENDER#{render_id} / ARTIFACT#{artifact_id}) to
  # serve the lookup. Infra-proposed — pending backend (contract owner) sign-off
  # on the exact GSI1PK/GSI1SK convention. Sparse: items without GSI1PK are
  # simply absent from the index.
  attribute {
    name = "GSI1PK"
    type = "S"
  }
  attribute {
    name = "GSI1SK"
    type = "S"
  }
  global_secondary_index {
    name            = "GSI1"
    hash_key        = "GSI1PK"
    range_key       = "GSI1SK"
    projection_type = "ALL"
  }

  # TTL for cleanup of FAILED / abandoned projects. §17 lists no expires_at field
  # on any item; enabling TTL is harmless (items without the attribute never
  # expire) and satisfies the M1 requirement. Backend sets expires_at (epoch
  # seconds) only on items it wants auto-purged.
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = merge(var.tags, { Purpose = "video-editor-state" })
}
