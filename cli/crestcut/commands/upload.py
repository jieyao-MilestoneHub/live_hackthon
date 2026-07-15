"""`crestcut upload` — put a video (presigned multipart) or a chat-log CSV."""
from __future__ import annotations

import os

from ..errors import UsageError
from ..upload import upload_chat, upload_video


def register(subparsers, parent):
    p = subparsers.add_parser("upload", parents=[parent],
                              help="upload a video or a chat-log CSV to a project")
    p.add_argument("project_id", metavar="PROJECT_ID")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--video", metavar="FILE", help="video file (presigned multipart upload)")
    src.add_argument("--chat", metavar="FILE", help="chat-log CSV (chat-source analysis)")
    p.add_argument("--source-duration-ms", type=int, dest="source_duration_ms",
                   help="video length in ms (chat flow: links the timebase so cuts stay in range)")
    p.set_defaults(_handler=_handle)


def _handle(ctx, args):
    if args.video:
        _require_file(args.video)
        ctx.printer.step(f"uploading video {os.path.basename(args.video)} …")
        result = upload_video(ctx.api, args.project_id, args.video,
                              on_progress=lambda pct: ctx.printer.debug(f"upload {pct}%"))
        ctx.printer.success(f"uploaded → {result.get('status')}")
        ctx.printer.data(result)
        return

    _require_file(args.chat)
    ctx.printer.step(f"uploading chat log {os.path.basename(args.chat)} …")
    session = upload_chat(ctx.api, args.project_id, args.chat)
    if args.source_duration_ms is not None:
        ctx.api.set_video_timebase(args.project_id, {"source_duration_ms": args.source_duration_ms})
    ctx.printer.success("chat log uploaded")
    ctx.printer.data({"project_id": args.project_id, "chat_key": session.get("key"),
                      "source_duration_ms": args.source_duration_ms})


def _require_file(path: str) -> None:
    if not os.path.isfile(path):
        raise UsageError(f"file not found: {path}")
