#!/usr/bin/env bash
# redrive_dlq.sh — move failed messages from a DLQ back to their source queue
# (WS4). The DLQs have no consumer, so when the depth alarm emails you, use this
# to replay the dropped records after fixing the cause.
#
# Usage:
#   ./scripts/redrive_dlq.sh <dlq-name-or-url> [max-per-second]
#
#   <dlq-name-or-url>   e.g. lang-live-analysis-dev-analysis-intake-dlq
#   [max-per-second]    optional redrive rate cap (default: unset = as fast as possible)
#
# Needs valid AWS creds in the env. Region defaults to us-east-1.
set -euo pipefail

DLQ="${1:?usage: redrive_dlq.sh <dlq-name-or-url> [max-per-second]}"
RATE="${2:-}"
REGION="${AWS_REGION:-us-east-1}"

# Accept a bare name or a full URL → resolve to the ARN start-message-move-task needs.
if [[ "$DLQ" == https://* ]]; then
  DLQ_URL="$DLQ"
else
  DLQ_URL="$(aws sqs get-queue-url --queue-name "$DLQ" --region "$REGION" --output text --query QueueUrl)"
fi
DLQ_ARN="$(aws sqs get-queue-attributes --queue-url "$DLQ_URL" \
  --attribute-names QueueArn --region "$REGION" --output text --query 'Attributes.QueueArn')"

echo ">>> redriving DLQ: $DLQ_ARN (region $REGION)"

# No DestinationArn → SQS redrives to each message's original source queue (the one
# that declared this DLQ as its redrive target). Add a rate cap if given.
ARGS=(--source-arn "$DLQ_ARN" --region "$REGION")
if [[ -n "$RATE" ]]; then
  ARGS+=(--max-number-of-messages-per-second "$RATE")
fi

TASK_HANDLE="$(aws sqs start-message-move-task "${ARGS[@]}" --output text --query TaskHandle)"
echo ">>> move task started. Handle: $TASK_HANDLE"
echo ">>> watch progress:  aws sqs list-message-move-tasks --source-arn '$DLQ_ARN' --region '$REGION'"
