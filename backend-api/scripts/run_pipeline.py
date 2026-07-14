"""Local stand-in for the Analysis Step Functions pipeline.

Simulates the (S3 upload -> EventBridge -> SQS -> Starter Lambda -> Step Functions)
trigger that doesn't exist locally: advances a project's state to ANALYZING, then
runs the Analysis + Composer workers to populate highlights + an initial timeline
and mark the project READY_TO_EDIT.

Usage (from backend-api/):
    python3 scripts/run_pipeline.py                       # new project, sample transcript
    python3 scripts/run_pipeline.py --target-ms 20000
    python3 scripts/run_pipeline.py --project-id project-abc --transcript path/to/transcript.json

Note: with USE_INMEMORY=1 (default) the repo is process-local — this seeds THIS
process only. For a live server demo across processes, run with USE_INMEMORY=0 and
a persistent DynamoDB so uvicorn reads the same table.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

# Make backend-api/ importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.validate import load_sample  # noqa: E402
from app.repository import get_repository  # noqa: E402
from app.settings import get_settings  # noqa: E402
from app.state import ProjectState, assert_project_transition  # noqa: E402
from app.storage import get_storage  # noqa: E402
from workers import analysis_worker, composer_worker, creative_worker, render_worker  # noqa: E402

# Path from CREATED to ANALYZING that the real S3-event pipeline would drive.
_TO_ANALYZING = [ProjectState.UPLOAD_PENDING, ProjectState.UPLOADING, ProjectState.ANALYZING]


def _advance(repo, project_id: str, target: ProjectState) -> None:
    current = ProjectState(repo.get_project(project_id)["status"])
    assert_project_transition(current, target)
    repo.update_project(project_id, {"status": target.value})


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the analysis+compose pipeline locally.")
    parser.add_argument("--project-id", default=None, help="existing project id (default: create one)")
    parser.add_argument("--transcript", default=None, help="path to a transcript.v1 JSON (default: sample)")
    parser.add_argument("--target-ms", type=int, default=30000, help="target duration ms (new project)")
    parser.add_argument("--render", action="store_true", help="also submit a render (Creative Planning -> QUEUED)")
    args = parser.parse_args()

    settings = get_settings()
    repo = get_repository()
    print(f"[pipeline] backend store: {'in-memory' if settings.use_inmemory else settings.dynamodb_table}")

    project_id = args.project_id
    if project_id is None:
        project_id = f"project-{uuid.uuid4().hex[:12]}"
        repo.create_project({
            "project_id": project_id,
            "tenant_id": "demo",
            "user_id": "cli",
            "status": ProjectState.CREATED.value,
            "target_duration_ms": args.target_ms,
            "source_bucket": settings.raw_bucket,
            "source_key": settings.source_key("demo", project_id),
            "latest_timeline_version": 0,
        })
        print(f"[pipeline] created project {project_id} (target={args.target_ms}ms)")

    if args.transcript:
        transcript = json.loads(Path(args.transcript).read_text(encoding="utf-8"))
    else:
        transcript = load_sample("transcript.sample.json")

    # Simulate the S3-event trigger walking the project into ANALYZING.
    for state in _TO_ANALYZING:
        _advance(repo, project_id, state)
    print("[pipeline] status -> ANALYZING")

    highlights = analysis_worker.run(repo, project_id, transcript)
    print(f"[pipeline] analysis -> {len(highlights['highlights'])} highlights, status -> COMPOSING")

    timeline = composer_worker.run(repo, project_id)
    print(
        f"[pipeline] compose  -> timeline v{timeline['version']}, "
        f"{len(timeline['clips'])} clips, actual={timeline['actual_duration_ms']}ms "
        f"(target={timeline['target_duration_ms']}ms), status -> READY_TO_EDIT"
    )

    if args.render:
        storage = get_storage()
        render = creative_worker.submit_render(repo, storage, project_id)
        print(
            f"[pipeline] plan     -> {render['render_id']}, status -> {render['status']}, "
            f"effect_seed={render['effect_seed']}, timeline v{render['timeline_version']}"
        )
        artifact = render_worker.run(repo, storage, project_id, render["render_id"])
        print(
            f"[pipeline] render   -> artifact {artifact['artifact_id']}, status -> READY, "
            f"{artifact['resolution']['width']}x{artifact['resolution']['height']}, "
            f"video_key={artifact['files']['video_key']}, status -> SUCCEEDED / ARTIFACT_READY"
        )

    print(f"[pipeline] DONE. project_id = {project_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
