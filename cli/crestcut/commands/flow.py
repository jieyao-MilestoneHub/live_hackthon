"""`crestcut clip` — the headline one-shot.

create → upload → analyze → compose → (render → download), in one command.
Chat mode runs fully offline (`crestcut up`); video/transcribe mode needs the
`dev` profile (real AWS Transcribe fires on the S3 upload event).
"""
from __future__ import annotations

import os

from ..errors import CrestcutError, UsageError
from ..poll import wait_project, wait_render
from ..upload import download_artifact, upload_chat, upload_video
from ..views import highlights_human, timeline_human
from .project import target_ms


def register(subparsers, parent):
    p = subparsers.add_parser(
        "clip",
        parents=[parent],
        help="one-shot: create → upload → analyze → compose → (render) a clip",
        description=(
            "Run the whole pipeline in one command. Chat mode (--chat) works fully "
            "offline against `crestcut up`; video mode (--video) uses Transcribe and "
            "needs --profile dev (real AWS)."
        ),
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--chat", metavar="FILE", help="chat-log CSV (offline-capable)")
    src.add_argument("--video", metavar="FILE", help="video file (transcribe; needs --profile dev)")
    p.add_argument("--seconds", type=int, default=30, help="target clip length in seconds, 1–60 (default 30)")
    p.add_argument("--source-duration-ms", type=int, dest="source_duration_ms",
                   help="video length in ms (chat mode: bounds detected windows)")
    p.add_argument("--title", help="project title")
    p.add_argument("--render", action="store_true", help="also render the clip")
    p.add_argument("--out", metavar="FILE", help="download the rendered clip to FILE (implies --render)")
    p.add_argument("--timeout", type=float, default=300.0, help="max seconds per wait phase (default 300)")
    p.set_defaults(_handler=_handle)


def _handle(ctx, args):
    pr = ctx.printer
    source = "chat" if args.chat else "transcribe"
    file_path = args.chat or args.video
    if not os.path.isfile(file_path):
        raise UsageError(f"file not found: {file_path}")

    body = {"target_duration_ms": target_ms(args.seconds), "analysis_source": source}
    if args.title:
        body["title"] = args.title
    project_id = ctx.api.create_project(body)["project_id"]
    pr.step(f"created project {project_id} (target {args.seconds}s, source={source})")

    if source == "chat":
        _run_chat(ctx, project_id, args)
    else:
        _run_video(ctx, project_id, args)

    highlights = ctx.api.get_highlights(project_id).get("highlights", [])
    try:
        timeline = ctx.api.get_timeline(project_id)
    except CrestcutError:
        timeline = None

    result = {
        "project_id": project_id,
        "status": ctx.api.get_project(project_id).get("status"),
        "highlights": highlights,
        "timeline": timeline,
    }

    if args.render or args.out:
        render = _render(ctx, project_id, args)
        result["render"] = render
        if args.out and render.get("status") == "SUCCEEDED" and render.get("artifact_id"):
            path = download_artifact(ctx.api, render["artifact_id"], args.out)
            pr.success(f"downloaded → {path}")
            result["download_path"] = path

    pr.success(f"done — {len(highlights)} highlight(s), project {project_id}")
    ctx.printer.data(result, human=_clip_human)


def _run_chat(ctx, project_id, args):
    pr = ctx.printer
    pr.step("uploading chat log …")
    upload_chat(ctx.api, project_id, args.chat)
    if args.source_duration_ms is not None:
        ctx.api.set_video_timebase(project_id, {"source_duration_ms": args.source_duration_ms})
    pr.step("analyzing chat …")
    analyze_body = {}
    if args.source_duration_ms is not None:
        analyze_body["source_duration_ms"] = args.source_duration_ms
    res = ctx.api.analyze(project_id, analyze_body)
    pr.note(f"  {res.get('highlight_count')} highlight(s) → {res.get('status')}")
    pr.step("composing timeline …")
    ctx.api.compose(project_id, {})


def _run_video(ctx, project_id, args):
    pr = ctx.printer
    pr.step("uploading video …")
    upload_video(ctx.api, project_id, args.video, on_progress=lambda pct: pr.debug(f"upload {pct}%"))
    pr.step("waiting for analysis (transcribe → compose) …")
    proj = wait_project(ctx.api, project_id, until={"READY_TO_EDIT", "ARTIFACT_READY"},
                        timeout=args.timeout, printer=pr)
    if proj.get("status") not in ("READY_TO_EDIT", "ARTIFACT_READY"):
        pr.warn(f"project is {proj.get('status')} — transcribe analysis needs --profile dev (real AWS)")


def _render(ctx, project_id, args):
    pr = ctx.printer
    pr.step("rendering …")
    created = ctx.api.create_render(project_id)
    render = wait_render(ctx.api, created["render_id"], timeout=args.timeout, printer=pr)
    pr.note(f"  render {render.get('status')}")
    return render


def _clip_human(p, result):
    print(f"project {result['project_id']}  [{result.get('status')}]")
    highlights_human(p, {"project_id": result["project_id"], "highlights": result.get("highlights", [])})
    if result.get("timeline"):
        timeline_human(p, result["timeline"])
    if result.get("download_path"):
        print(f"clip → {result['download_path']}")
