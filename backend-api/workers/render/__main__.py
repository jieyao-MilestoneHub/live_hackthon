"""AWS Batch container entrypoint for the FFmpeg render worker.

The render Step Functions submits a Batch job with env PROJECT_ID / RENDER_ID
(+ USE_INMEMORY=0, RENDER_ENCODER=ffmpeg, DYNAMODB_TABLE, RAW/WORK/OUTPUT_BUCKET).
This reuses the SAME orchestration as offline (render_worker.run) — only the
encoder differs (FFmpegEncoder, selected by RENDER_ENCODER=ffmpeg).

    docker build -f backend-api/Dockerfile.render -t <ecr>:render .   # from repo root
    # Batch job runs: python -m workers.render
"""
from __future__ import annotations

import os
import sys

from app.repository import get_repository
from app.storage import get_storage
from workers import render_worker


def main() -> int:
    project_id = os.environ.get("PROJECT_ID")
    render_id = os.environ.get("RENDER_ID")
    if not project_id or not render_id:
        print("PROJECT_ID and RENDER_ID env vars are required", file=sys.stderr)
        return 2

    repo = get_repository()
    storage = get_storage()
    artifact = render_worker.run(repo, storage, project_id, render_id)
    print(f"published artifact {artifact['artifact_id']} for render {render_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
