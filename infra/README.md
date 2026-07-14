# infra — 浪 LIVE (Terraform, dev)

Terraform for the 浪 LIVE walking skeleton (AI livestream highlight clipping).
This scaffolds the `dev` environment; the full target architecture lives in
[`docs/aws-infra.md`](../docs/aws-infra.md).

- **Region:** `us-east-1` (N. Virginia)
- **State:** local for now (no remote backend). Add an S3/DynamoDB backend
  before sharing state across the team.

## What's here

```
infra/
├── environments/
│   └── dev/                 # root module — wires the three modules below
├── modules/
│   ├── foundation/          # S3 raw/work/output buckets + VideoJobs DynamoDB (§5, §6)
│   ├── frontend-cdn/        # private S3 + CloudFront (OAC) for Next.js static export
│   └── backend-apprunner/   # ECR repo + App Runner service for the FastAPI container
├── deploy.sh                # dev deploy runbook (ordering: ECR → push → full apply → frontend)
└── README.md
```

### Deployment decisions (locked)
- **Frontend:** Next.js static export → S3 (private) + CloudFront (OAC).
  CloudFront rewrites 403/404 → `/index.html` (200) so client routes like
  `/jobs?id=...` resolve.
- **Backend:** FastAPI container → ECR → AWS App Runner (port 8080, health `/health`).
- **Foundation:** three video buckets + a single-table `VideoJobs` DynamoDB
  (PK/SK, GSI1, GSI2, TTL on `expires_at`). Scaffolded now, wired to the
  pipeline later.

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
`apprunner_service_url`, `ecr_repository_url`, plus foundation outputs
(`raw_bucket`, `work_bucket`, `output_bucket`, `dynamodb_table_name`).

## Not yet built (see docs/aws-infra.md §12)

The pipeline modules are still TODO: `eventing` (EventBridge + SQS intake),
`orchestration` (Step Functions), `lambda-light` (light worker pool),
`batch-heavy` (AWS Batch + FFmpeg / ECR image), `observability`, and `security`
(KMS keys, per-stage IAM policies). The foundation buckets/table here are the
inputs those modules will consume.
