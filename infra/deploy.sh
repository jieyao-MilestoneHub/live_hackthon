#!/usr/bin/env bash
#
# deploy.sh — dev deploy runbook for the 浪 LIVE walking skeleton.
#
# Resolves the App-Runner-needs-an-image-first ordering problem:
#   App Runner can't create a service against an ECR image that doesn't exist
#   yet, but the ECR repo itself is Terraform-managed. So we create ECR first,
#   push the image, then run the full apply with the real image URI.
#
# Run from the infra/ directory:   ./deploy.sh
# This is a RUNBOOK: it is safe to read top-to-bottom and run step by step.
# It needs real AWS credentials — it is NOT required for `terraform validate`.
#
# Prereqs: terraform, aws cli (configured for us-east-1), docker, node/npm.
set -euo pipefail

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
ENV_DIR="environments/dev"
REGION="${AWS_REGION:-us-east-1}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

echo "==> 浪 LIVE dev deploy | region=${REGION} tag=${IMAGE_TAG}"

# ----------------------------------------------------------------------------
# 0. Init
# ----------------------------------------------------------------------------
echo "==> [0/5] terraform init"
terraform -chdir="${ENV_DIR}" init

# ----------------------------------------------------------------------------
# 1. Create the ECR repository FIRST (targeted apply)
# ----------------------------------------------------------------------------
echo "==> [1/5] create ECR repository (targeted apply)"
terraform -chdir="${ENV_DIR}" apply \
  -target=module.backend.aws_ecr_repository.backend \
  -auto-approve

ECR_URL="$(terraform -chdir="${ENV_DIR}" output -raw ecr_repository_url)"
echo "    ECR_URL=${ECR_URL}"

# ----------------------------------------------------------------------------
# 2. Build + push the backend image
#    Build context is the repo root so the Dockerfile can COPY backend-api/.
#    Paths are relative to infra/ (where this script runs).
# ----------------------------------------------------------------------------
echo "==> [2/5] docker build + push backend image"
aws ecr get-login-password --region "${REGION}" \
  | docker login --username AWS --password-stdin "${ECR_URL%%/*}"

docker build -f ../backend-api/Dockerfile -t "${ECR_URL}:${IMAGE_TAG}" ..
docker push "${ECR_URL}:${IMAGE_TAG}"

# ----------------------------------------------------------------------------
# 3. Full apply with the real image (creates/updates App Runner, frontend, etc.)
# ----------------------------------------------------------------------------
echo "==> [3/5] full terraform apply (App Runner + frontend + foundation)"
terraform -chdir="${ENV_DIR}" apply \
  -var "backend_image=${ECR_URL}:${IMAGE_TAG}" \
  -auto-approve

APPRUNNER_URL="$(terraform -chdir="${ENV_DIR}" output -raw apprunner_service_url)"
FRONTEND_BUCKET="$(terraform -chdir="${ENV_DIR}" output -raw frontend_bucket)"
DIST_ID="$(terraform -chdir="${ENV_DIR}" output -raw cloudfront_distribution_id)"
echo "    APPRUNNER_URL=${APPRUNNER_URL}"
echo "    FRONTEND_BUCKET=${FRONTEND_BUCKET}"
echo "    DIST_ID=${DIST_ID}"

# ----------------------------------------------------------------------------
# 4. Build the frontend static export against the live backend, sync to S3
# ----------------------------------------------------------------------------
echo "==> [4/5] build + sync frontend"
( cd ../frontend-web && NEXT_PUBLIC_API_BASE_URL="${APPRUNNER_URL}" npm run build )
aws s3 sync ../frontend-web/out "s3://${FRONTEND_BUCKET}" --delete

# ----------------------------------------------------------------------------
# 5. Invalidate the CloudFront cache so the new export is served immediately
# ----------------------------------------------------------------------------
echo "==> [5/5] CloudFront invalidation"
aws cloudfront create-invalidation --distribution-id "${DIST_ID}" --paths '/*'

echo "==> Done."
echo "    Frontend : https://$(terraform -chdir="${ENV_DIR}" output -raw cloudfront_domain)"
echo "    Backend  : ${APPRUNNER_URL}"
