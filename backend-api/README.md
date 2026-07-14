# backend-api — 浪 LIVE Job API

FastAPI walking skeleton for the AI livestream highlight-clipping service.
Implements `contracts/openapi.yaml` with an in-memory, synchronous **stub**
pipeline so the frontend can integrate immediately. Rule-based highlight
detection (`analysis/`) is real; S3 upload + Step Functions rendering is stubbed
and marked with `TODO(team)` in `app/main.py`.

## Layout

```
backend-api/
  app/            FastAPI app + pydantic schemas (mirror openapi.yaml)
  analysis/       transcript.v1 -> highlights.v1 detector + jsonschema validators
  tests/          pytest (contract invariants + API smoke tests)
  Dockerfile      build from REPO ROOT (needs backend-api/ and contracts/)
```

The app reads the shared JSON Schemas and sample transcript from the sibling
`contracts/` directory. Resolution order (see `analysis/validate.py`):
`CONTRACTS_DIR` env → `<repo_root>/contracts` → `<backend-api>/contracts`
(container) → `<cwd>/contracts`.

## Run locally

```bash
cd backend-api
python3 -m venv .venv
source .venv/Scripts/activate        # Windows Git Bash; use bin/activate on Linux/macOS
python3 -m pip install -r requirements-dev.txt

# start the API
uvicorn app.main:app --port 8080
```

Smoke test:

```bash
curl -s localhost:8080/health
curl -s -X POST localhost:8080/jobs \
  -H 'content-type: application/json' \
  -d '{"filename":"stream.mp4"}'
```

Interactive docs: <http://localhost:8080/docs>

## Test

```bash
cd backend-api
python3 -m pytest
```

## Docker

The build context must be the **repo root** so the image can `COPY` both
`backend-api/` and the shared `contracts/`:

```bash
# from the repo root
docker build -f backend-api/Dockerfile -t backend .
docker run --rm -p 8080:8080 backend
```

The image sets `CONTRACTS_DIR=/app/contracts`.

## Endpoints

| Method | Path                                             | Notes                                  |
| ------ | ------------------------------------------------ | -------------------------------------- |
| GET    | `/health`                                        | `{"status":"ok","version":...}`        |
| POST   | `/jobs`                                           | 201; stub runs pipeline → `SUCCEEDED`  |
| GET    | `/jobs/{job_id}`                                  | 200 / 404                              |
| GET    | `/jobs/{job_id}/artifacts/{clip_id}/download`     | stub URL, `expires_in_sec: 900`; 404   |

## TODO(team)

- Replace the stub in `POST /jobs` with real S3 upload + Step Functions
  (transcribe → normalize to `transcript.v1` → analyze → render).
- Return a real S3 presigned / CloudFront signed URL from the download endpoint.
- Swap the in-memory `_JOBS` dict for a durable store (e.g. DynamoDB).
- Tighten CORS `allow_origins` to the deployed frontend origin(s).
