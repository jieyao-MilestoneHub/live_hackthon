# auth: Amazon Cognito user pool + public web client for the editor.
# Authoritative: docs/demand.md §3 (Authentication) / §4. Flow:
#   Browser → Cognito (login, SRP)  →  JWT
#   Browser → Editor API (backend) with the JWT; backend verifies it against
#   this pool's JWKS (issuer = https://<user_pool_endpoint>).
#
# ⚠️ SCP probe: Cognito is this workshop account's first use of the service and
# the account's SCP is known to block some services (App Runner, public Lambda
# URLs). Run a create→delete probe BEFORE apply — see infra/README.md.

resource "aws_cognito_user_pool" "editor" {
  name = "${var.project}-editor-${var.env}"

  # Email is the login identifier; Cognito verifies it on sign-up.
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  password_policy {
    minimum_length    = 8
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = false # MVP: easier demo sign-ups; tighten for prod.
  }

  # MVP: no MFA. Cognito-hosted email for verification (low volume, dev only).
  mfa_configuration = "OFF"

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  email_configuration {
    email_sending_account = "COGNITO_DEFAULT"
  }

  tags = merge(var.tags, { Purpose = "editor-auth" })
}

# Content-moderation review roles. Membership lands in the JWT ``cognito:groups``
# claim, which app/auth.py reads into Principal.roles and require_moderator gates
# the /moderation/override endpoint on. Add a user to a group with:
#   aws cognito-idp admin-add-user-to-group --user-pool-id <id> \
#       --username <email> --group-name moderator
resource "aws_cognito_user_group" "moderator" {
  name         = "moderator"
  user_pool_id = aws_cognito_user_pool.editor.id
  description  = "Can review/override content-moderation verdicts."
}

resource "aws_cognito_user_group" "admin" {
  name         = "admin"
  user_pool_id = aws_cognito_user_pool.editor.id
  description  = "Administrators (superset of moderator)."
}

# Public SPA client — no secret (the browser cannot keep one). Uses SRP; no
# hosted UI / OAuth callback in MVP (frontend uses amazon-cognito-identity-js).
# USER_PASSWORD is enabled to make scripted/demo sign-in trivial.
resource "aws_cognito_user_pool_client" "web" {
  name         = "${var.project}-editor-${var.env}-web"
  user_pool_id = aws_cognito_user_pool.editor.id

  generate_secret = false

  explicit_auth_flows = [
    "ALLOW_USER_SRP_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
    "ALLOW_USER_PASSWORD_AUTH",
  ]

  supported_identity_providers = ["COGNITO"]

  # Don't leak whether an email exists on failed auth.
  prevent_user_existence_errors = "ENABLED"

  access_token_validity  = 60
  id_token_validity      = 60
  refresh_token_validity = 30
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
}
