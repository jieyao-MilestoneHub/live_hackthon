"""`crestcut compose` — (re)build the timeline from highlights."""
from __future__ import annotations

from .. import views


def register(subparsers, parent):
    p = subparsers.add_parser("compose", parents=[parent],
                              help="(re)build the timeline from highlights")
    p.add_argument("project_id", metavar="PROJECT_ID")
    p.add_argument("--seconds", type=int, help="target clip length in seconds (default: project target)")
    p.add_argument("--lock", nargs="*", metavar="ID", default=None, help="highlight ids to keep")
    p.add_argument("--exclude", nargs="*", metavar="ID", default=None, help="highlight ids to drop")
    p.add_argument("--show", action="store_true", help="also fetch + print the new timeline")
    p.set_defaults(_handler=_handle)


def _handle(ctx, args):
    body = {}
    if args.seconds is not None:
        body["target_duration_ms"] = int(args.seconds) * 1000
    if args.lock is not None:
        body["locked_highlight_ids"] = args.lock
    if args.exclude is not None:
        body["excluded_highlight_ids"] = args.exclude
    result = ctx.api.compose(args.project_id, body)
    version = result.get("timeline_version")
    ctx.printer.success(f"composed timeline v{version}")
    if args.show:
        ctx.printer.data(ctx.api.get_timeline(args.project_id, version), human=views.timeline_human)
    else:
        ctx.printer.data(result)
