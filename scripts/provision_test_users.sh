#!/usr/bin/env bash
# provision_test_users.sh — bulk-create on-site test accounts in the editor
# Cognito user pool (WS5). The pool is admin-create-only (no public signup), so
# only accounts made here can drive the pipeline — this is how we hand out
# "現場測試帳密" for ~300 testers while keeping everyone else out.
#
# Usage:
#   ./scripts/provision_test_users.sh [-n COUNT] [-p PASSWORD] [-d DOMAIN] [-m N_MODERATORS]
#
#   -n COUNT          how many users (default 30)
#   -p PASSWORD       shared permanent password (default: Live<YYYY>!demo — meets the
#                     pool policy: >=8, upper+lower+digit). Override for a real event.
#   -d DOMAIN         email domain (default example.com) → user{NNN}@DOMAIN
#   -m N_MODERATORS   grant the first N users the 'moderator' group (default 1)
#
# Pool id/region come from terraform output (no hardcoding). Needs valid AWS creds
# in the env (temp creds sourced from a scratchpad file — never commit them).
set -euo pipefail

COUNT=30
PASSWORD=""
DOMAIN="example.com"
N_MODERATORS=1
DEV_DIR="infra/environments/dev"

while getopts "n:p:d:m:" opt; do
  case "$opt" in
    n) COUNT="$OPTARG" ;;
    p) PASSWORD="$OPTARG" ;;
    d) DOMAIN="$OPTARG" ;;
    m) N_MODERATORS="$OPTARG" ;;
    *) echo "usage: $0 [-n COUNT] [-p PASSWORD] [-d DOMAIN] [-m N_MODERATORS]" >&2; exit 2 ;;
  esac
done

# Default password carries a year so it satisfies the policy without a real secret
# in the repo. YEAR is passed in (Date.now is fine in a shell) — override with -p.
if [[ -z "$PASSWORD" ]]; then
  PASSWORD="Live$(date +%Y)!demo"
fi

POOL_ID="$(terraform -chdir="$DEV_DIR" output -raw cognito_user_pool_id)"
REGION="$(terraform -chdir="$DEV_DIR" output -raw region 2>/dev/null || echo us-east-1)"

echo ">>> pool=$POOL_ID region=$REGION count=$COUNT domain=$DOMAIN moderators=$N_MODERATORS"
echo ">>> shared password: $PASSWORD"

for i in $(seq 1 "$COUNT"); do
  n="$(printf '%03d' "$i")"
  email="user${n}@${DOMAIN}"

  # Idempotent: skip if the user already exists.
  if aws cognito-idp admin-get-user --user-pool-id "$POOL_ID" --username "$email" \
        --region "$REGION" >/dev/null 2>&1; then
    echo "  = $email (exists, skip create)"
  else
    aws cognito-idp admin-create-user \
      --user-pool-id "$POOL_ID" --username "$email" --region "$REGION" \
      --user-attributes Name=email,Value="$email" Name=email_verified,Value=true \
      --message-action SUPPRESS >/dev/null
    echo "  + $email (created)"
  fi

  # Permanent password so the account is login-ready (no FORCE_CHANGE_PASSWORD).
  aws cognito-idp admin-set-user-password \
    --user-pool-id "$POOL_ID" --username "$email" --region "$REGION" \
    --password "$PASSWORD" --permanent >/dev/null

  # First N_MODERATORS get the moderator group (content-review override authority).
  if [[ "$i" -le "$N_MODERATORS" ]]; then
    aws cognito-idp admin-add-user-to-group \
      --user-pool-id "$POOL_ID" --username "$email" --region "$REGION" \
      --group-name moderator >/dev/null
    echo "    ^ granted moderator"
  fi
done

echo ">>> done. Testers log in with user001..user${COUNT}@${DOMAIN} / '$PASSWORD'."
