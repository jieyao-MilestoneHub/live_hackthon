"""`crestcut analyze` — run chat-log analysis (chat source → highlights)."""
from __future__ import annotations


def register(subparsers, parent):
    p = subparsers.add_parser("analyze", parents=[parent],
                              help="run chat-log analysis for a chat-source project")
    p.add_argument("project_id", metavar="PROJECT_ID")
    p.add_argument("--source-duration-ms", type=int, dest="source_duration_ms",
                   help="video length in ms (bounds the detected windows)")
    p.add_argument("--video-start-epoch-ms", type=int, dest="video_start_epoch_ms",
                   help="epoch ms of video 0:00 (chat epoch ↔ video-relative ms)")
    p.add_argument("--chat-key", dest="chat_key",
                   help="override the Raw-bucket key of the uploaded chat CSV")
    p.set_defaults(_handler=_handle)


def _handle(ctx, args):
    body = {}
    if args.source_duration_ms is not None:
        body["source_duration_ms"] = args.source_duration_ms
    if args.video_start_epoch_ms is not None:
        body["video_start_epoch_ms"] = args.video_start_epoch_ms
    if args.chat_key:
        body["chat_key"] = args.chat_key
    result = ctx.api.analyze(args.project_id, body)
    ctx.printer.success(
        f"{result.get('highlight_count')} highlight(s) → {result.get('status')}"
    )
    ctx.printer.data(result)
