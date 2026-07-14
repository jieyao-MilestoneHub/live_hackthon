#!/usr/bin/env bash
#
# deploy.sh — dev deploy runbook for 浪 LIVE (M2: real cloud pipeline).
#
# Control plane = Lambda(Mangum) + API Gateway (App Runner is SCP-blocked).
# Data plane    = analysis Step Functions (Transcribe/Bedrock) + render
#                 Step Functions (AWS Batch/FFmpeg). Two container images:
#                   backend  :lambda  (Dockerfile.lambda)  — API + worker Lambdas
#                   render   :render  (Dockerfile.render)  — FFmpeg Batch job
#
# Run from the infra/ directory:  ./deploy.sh
# Needs real AWS creds (source a scratchpad env file; NEVER commit them).
# NOT required for `terraform validate`.
#
# ⚠️  BEFORE the first apply, run the SCP create→delete probes for the NEW
#     services (Transcribe, Bedrock, SQS, EventBridge, Step Functions, AWS Batch)
#     and enable Bedrock Nova model access in the us-east-1 console. If AWS Batch
#     (Fargate/ECS/EC2) is SCP-blocked, see the render-batch fallback notes.
#     Show `terraform plan` (esp. IAM/destroy) before approving.
#
# Prereqs: terraform 1.10.5, aws cli (us-east-1), docker, node/npm.
set -euo pipefail

ENV_DIR="environments/dev"
REGION="${AWS_REGION:-us-east-1}"
ROOT_TAG_LAMBDA="lambda"
ROOT_TAG_RENDER="render"

echo "==> 浪 LIVE dev deploy | region=${REGION}"

# ----------------------------------------------------------------------------
# 0. Init (remote S3 backend + DynamoDB lock)
# ----------------------------------------------------------------------------
echo "==> [0/6] terraform init"
terraform -chdir="${ENV_DIR}" init

# ----------------------------------------------------------------------------
# 1. Create BOTH ECR repos first (targeted apply) so we can push images.
# ----------------------------------------------------------------------------
echo "==> [1/6] create ECR repos (backend + render)"
terraform -chdir="${ENV_DIR}" apply -auto-approve \
  -target=module.backend.aws_ecr_repository.backend \
  -target=module.render_ecr.aws_ecr_repository.render

BACKEND_ECR="$(terraform -chdir="${ENV_DIR}" output -raw ecr_repository_url)"
RENDER_ECR="$(terraform -chdir="${ENV_DIR}" output -raw render_ecr_repository_url)"
echo "    BACKEND_ECR=${BACKEND_ECR}"
echo "    RENDER_ECR=${RENDER_ECR}"

# ----------------------------------------------------------------------------
# 2. Build + push both images. Build context is the repo ROOT so the
#    Dockerfiles can COPY both backend-api/ and contracts/.
# ----------------------------------------------------------------------------
echo "==> [2/6] docker build + push backend(:lambda) and render(:render)"
aws ecr get-login-password --region "${REGION}" \
  | docker login --username AWS --password-stdin "${BACKEND_ECR%%/*}"

docker build -f ../backend-api/Dockerfile.lambda -t "${BACKEND_ECR}:${ROOT_TAG_LAMBDA}" ..
docker push "${BACKEND_ECR}:${ROOT_TAG_LAMBDA}"

docker build -f ../backend-api/Dockerfile.render -t "${RENDER_ECR}:${ROOT_TAG_RENDER}" ..
docker push "${RENDER_ECR}:${ROOT_TAG_RENDER}"

# ----------------------------------------------------------------------------
# 3. Full apply with the real images (control plane + both data planes).
#    ALWAYS pass backend_lambda_image so the backend never reverts to the
#    placeholder. Review the plan (IAM / any destroy) before approving.
# ----------------------------------------------------------------------------
echo "==> [3/6] terraform plan (review IAM + Batch before approving)"
terraform -chdir="${ENV_DIR}" plan \
  -var "backend_lambda_image=${BACKEND_ECR}:${ROOT_TAG_LAMBDA}" \
  -var "render_image=${RENDER_ECR}:${ROOT_TAG_RENDER}"

read -r -p "==> apply the plan above? [y/N] " ok
[ "${ok}" = "y" ] || { echo "aborted"; exit 1; }

terraform -chdir="${ENV_DIR}" apply -auto-approve \
  -var "backend_lambda_image=${BACKEND_ECR}:${ROOT_TAG_LAMBDA}" \
  -var "render_image=${RENDER_ECR}:${ROOT_TAG_RENDER}"

API_URL="$(terraform -chdir="${ENV_DIR}" output -raw backend_api_endpoint)"
FRONTEND_BUCKET="$(terraform -chdir="${ENV_DIR}" output -raw frontend_bucket)"
DIST_ID="$(terraform -chdir="${ENV_DIR}" output -raw cloudfront_distribution_id)"
COGNITO_CLIENT="$(terraform -chdir="${ENV_DIR}" output -raw cognito_user_pool_client_id)"
echo "    API_URL=${API_URL}"

# ----------------------------------------------------------------------------
# 4. Build the frontend static export against the live backend + Cognito.
# ----------------------------------------------------------------------------
echo "==> [4/6] build frontend"
( cd ../frontend-web \
  && NEXT_PUBLIC_API_BASE_URL="${API_URL}" \
     NEXT_PUBLIC_COGNITO_CLIENT_ID="${COGNITO_CLIENT}" \
     NEXT_PUBLIC_COGNITO_REGION="${REGION}" \
     npm run build )

# ----------------------------------------------------------------------------
# 5. Sync to S3
# ----------------------------------------------------------------------------
echo "==> [5/6] sync frontend to S3"
aws s3 sync ../frontend-web/out "s3://${FRONTEND_BUCKET}" --delete

# ----------------------------------------------------------------------------
# 6. Invalidate CloudFront
# ----------------------------------------------------------------------------
echo "==> [6/6] CloudFront invalidation"
aws cloudfront create-invalidation --distribution-id "${DIST_ID}" --paths '/*'

echo "==> Done."
echo "    Frontend : https://$(terraform -chdir="${ENV_DIR}" output -raw cloudfront_domain)"
echo "    Backend  : ${API_URL}"
