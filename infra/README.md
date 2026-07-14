# infra — 浪 LIVE (Terraform, dev)

Terraform for the 浪 LIVE walking skeleton (AI livestream highlight clipping).
This scaffolds the `dev` environment; the full target architecture lives in
the [cloud architecture diagram](../docs/live_hackathon_arch.png) and [`docs/M2-handoff.md`](../docs/M2-handoff.md).

- **Region:** `us-east-1` (N. Virginia)
- **State:** **remote** — S3 `lang-live-tfstate-979287128595` (key `dev/terraform.tfstate`,
  versioning/BPA/SSE) + DynamoDB lock `lang-live-tflock`. Bootstrapped out-of-band
  (aws cli), NOT managed by Terraform. Backend config in `environments/dev/providers.tf`.

## What's here

```
infra/
├── environments/
│   └── dev/                 # root module — wires the modules below
├── modules/
│   ├── storage-editor/      # video-editor raw/work/output S3 buckets (demand.md §16)
│   ├── state-table/         # VideoEditor DynamoDB single table (demand.md §17)
│   ├── auth/                # Cognito user pool + public web client (demand.md §3/§4)
│   ├── frontend-cdn/        # private S3 + CloudFront (OAC) for Next.js static export
│   ├── backend-ecr/         # ECR repo for the FastAPI image (App Runner SCP-blocked)
│   ├── backend-lambda/      # Lambda container + API Gateway HTTP API (App Runner alt)
│   ├── analysis-workflow/   # M2.1 worker Lambdas + Analysis Step Functions (Transcribe/Bedrock)
│   ├── analysis-ingress/    # M2.1 S3→EventBridge→SQS(+DLQ)→idempotent Starter Lambda
│   ├── render-ecr/          # M2.2 ECR repo for the FFmpeg render image
│   ├── render-batch/        # M2.2 AWS Batch (Fargate) compute env + queue + job def
│   └── render-workflow/     # M2.2 Render Step Functions (plan → Batch submitJob.sync)
├── deploy.sh                # dev deploy runbook (ECR ×2 → push → plan/apply → frontend)
└── README.md
```

> **M2 (real cloud pipeline) is now wired.** The analysis + render planes above
> add NEW AWS services (Transcribe, Bedrock, SQS, EventBridge, Step Functions,
> AWS Batch). **Run an SCP create→delete probe for each before the first apply**
> and enable Bedrock Nova model access in the console. AWS Batch (Fargate) is the
> highest SCP risk — see `modules/render-batch/main.tf` for the fallback path.

### Deployment decisions (locked)
- **Frontend:** Next.js static export → S3 (private) + CloudFront (OAC).
  CloudFront rewrites 403/404 → `/index.html` (200) so client routes resolve.
- **Backend:** FastAPI container → ECR → Lambda + API Gateway HTTP API
  (App Runner is SCP-blocked in the workshop account).
- **Storage (M1, §16):** three `video-editor-{raw,work,output}` buckets — Block
  Public Access, BucketOwnerEnforced, versioning, SSE-S3, lifecycle stubs.
  Input/output are separate buckets to avoid event loops. Replaces the legacy
  `foundation` `video-{raw,work,output}` buckets (M0 job model).
- **State (M1, §17):** single-table `VideoEditor` DynamoDB (PK=`PROJECT#{id}`,
  SK per entity, `GSI1` for id-only render/artifact lookup, TTL on `expires_at`,
  PAY_PER_REQUEST, PITR). Replaces the legacy `VideoJobs` table.
  ⚠️ `GSI1` is infra-proposed (demand.md §17 defines no GSI) to serve the §4
  `GET /renders/{id}` and `GET /artifacts/{id}` endpoints — pending backend
  (contract owner) sign-off on the `GSI1PK/GSI1SK` convention.
- **Auth (M1, §3/§4):** Cognito user pool + public SPA client (no secret, SRP).
  Backend verifies JWTs against the pool's JWKS. No hosted UI in MVP.
  ⚠️ Cognito is the account's first use of the service — run the SCP probe below
  before `apply`.

### SCP probe (before first apply of a new service)
The workshop account's SCP blocks some services (confirmed: App Runner, public
Lambda Function URLs). S3 and DynamoDB are already proven. **Cognito is new** —
probe create→delete before relying on it:

```bash
POOL=$(aws cognito-idp create-user-pool --pool-name scp-probe --query 'UserPool.Id' --output text) \
  && echo "Cognito allowed: $POOL" \
  && aws cognito-idp delete-user-pool --user-pool-id "$POOL"
```

If this fails with an SCP/authorization error, stop and discuss a fallback
(e.g. a stubbed auth for the MVP demo) before applying the `auth` module.

## Version pinning (why not the latest?)

`docs/aws-infra.md` §12 recommends **Terraform ~> 1.15 / AWS provider ~> 6.54**
(latest as of 2026-07-14). This repo intentionally pins **lower**:

```hcl
required_version = ">= 1.10"          # local CLI is 1.10.5
aws = { version = "~> 5.0" }          # avoid provider 6.x schema/behavior drift
```

This matches the installed toolchain so `plan`/`apply` behave predictably on
dev machines. Bump both together in a dedicated PR once the team upgrades local
Terraform. The rationale is also recorded as a comment in
`environments/dev/providers.tf`.

## Validate / plan

```bash
cd infra/environments/dev
terraform init -backend=false      # downloads the AWS provider; no state needed
terraform fmt -recursive ..        # format all infra files
terraform validate                 # -> "Success! The configuration is valid."
```

`terraform validate` does not call AWS, so no credentials are required.
For a real `plan`/`apply` you need AWS credentials for `us-east-1`:

```bash
terraform init                     # (with a real backend or local state)
terraform plan
```

## Deploy (order matters)

App Runner needs an image before it can start, but ECR is Terraform-managed —
so ECR is created first, the image is pushed, then the full apply runs. This is
automated in [`deploy.sh`](./deploy.sh) (run from `infra/`):

1. Targeted apply of `module.backend.aws_ecr_repository.backend` (create ECR).
2. `docker build -f ../backend-api/Dockerfile ..` + `docker push` to ECR.
3. Full `terraform apply -var backend_image=<ecr-url>:latest`.
4. `npm run build` the frontend (with `NEXT_PUBLIC_API_BASE_URL` = App Runner
   URL) + `aws s3 sync ../frontend-web/out s3://<frontend-bucket> --delete`.
5. `aws cloudfront create-invalidation --paths '/*'`.

```bash
cd infra
./deploy.sh
```

## Outputs

`cloudfront_domain`, `frontend_bucket`, `cloudfront_distribution_id`,
`backend_api_endpoint`, `ecr_repository_url`, plus M1 storage/state/auth outputs:
`raw_bucket`, `work_bucket`, `output_bucket` (video-editor buckets),
`dynamodb_table_name` (`VideoEditor-dev`), and `cognito_user_pool_id`,
`cognito_user_pool_endpoint`, `cognito_user_pool_client_id`.

## Not yet built (M2+, see infra/ROADMAP.md)

The pipeline modules are still TODO: `eventing` (EventBridge on raw `source/`
ObjectCreated → SQS `analysis-intake` + DLQ), `orchestration` (Step Functions
analysis & render workflows), `ai-task` worker pool (Lambda), `render-job`
(AWS Batch + FFmpeg / ECR image), observability, and per-stage IAM/KMS. The
M1 `storage-editor` buckets and `state-table` are the inputs those modules
will consume.
