"""浪 LIVE Job API — FastAPI walking skeleton.

M0 goal: expose the contract in ``contracts/openapi.yaml`` with an in-memory,
synchronous stub so the frontend can integrate immediately. The real pipeline
(S3 upload -> Step Functions: transcribe -> analyze -> render) replaces the stub
marked below.

Deploy target: container image (ECR) -> AWS App Runner.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from analysis import detect_highlights
from analysis.validate import load_sample
from app.schemas import (
    Clip,
    JobCreate,
    JobCreated,
    JobState,
    JobStatus,
    UploadInfo,
)

VERSION = "0.1.0"

app = FastAPI(title="浪 LIVE Job API", version=VERSION)

# Skeleton: wide-open CORS so any frontend dev origin can call us.
# TODO(team): tighten allow_origins to the deployed frontend origin(s).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory job store. TODO(team): replace with DynamoDB / durable state.
_JOBS: dict[str, JobStatus] = {}

# Sample transcript name bundled with the contracts (used by the stub pipeline).
_SAMPLE_TRANSCRIPT = "transcript.sample.json"


def _highlight_to_clip(h: dict[str, Any]) -> Clip:
    """Map a highlights.v1 highlight item onto the API Clip model."""
    return Clip(
        clip_id=h["clip_id"],
        start_sec=h["start_sec"],
        end_sec=h["end_sec"],
        score=h.get("score"),
        reason=h.get("reason"),
        title=h.get("title"),
        download_ready=True,  # stub renders are considered ready immediately
    )


def _run_stub_pipeline(job_id: str) -> list[Clip]:
    """Synchronously simulate the pipeline for the walking skeleton.

    TODO(team): replace stub with real S3 upload + Step Functions pipeline
    (transcribe -> normalize to transcript.v1 -> analyze -> render clips).
    """
    transcript = load_sample(_SAMPLE_TRANSCRIPT)
    # Carry the created job_id through so the analysis output is tied to this job.
    transcript = {**transcript, "job_id": job_id}
    result = detect_highlights(transcript)
    return [_highlight_to_clip(h) for h in result["highlights"]]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": VERSION}


@app.post("/jobs", response_model=JobCreated, status_code=201)
def create_job(body: JobCreate) -> JobCreated:
    job_id = f"job_{uuid.uuid4().hex}"

    # --- Skeleton stub: run the whole pipeline inline and mark SUCCEEDED. ---
    # TODO(team): replace stub with real S3 upload + Step Functions pipeline.
    clips = _run_stub_pipeline(job_id)
    _JOBS[job_id] = JobStatus(
        job_id=job_id,
        status=JobState.SUCCEEDED,
        current_stage="finalized",
        progress=100,
        highlights=clips,
    )

    # Stub upload info; real impl returns an S3 presigned multipart target.
    upload = UploadInfo(
        method="PUT",
        url=f"http://localhost:8080/stub-upload/{job_id}",
        key=f"tenant={body.tenant_id or 'demo'}/job={job_id}/input/{body.filename}",
    )
    return JobCreated(job_id=job_id, status=JobState.SUCCEEDED, upload=upload)


@app.get("/jobs/{job_id}", response_model=JobStatus)
def get_job(job_id: str) -> JobStatus:
    job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@app.get("/jobs/{job_id}/artifacts/{clip_id}/download")
def get_download_url(job_id: str, clip_id: str) -> dict[str, Any]:
    job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if not any(c.clip_id == clip_id for c in job.highlights):
        raise HTTPException(status_code=404, detail="clip not found")

    # TODO(team): return a real S3 presigned GET URL (MVP) or CloudFront signed
    # URL (enterprise) for the rendered clip artifact.
    return {
        "url": f"http://localhost:8080/stub-download/{job_id}/{clip_id}.mp4",
        "expires_in_sec": 900,
    }
