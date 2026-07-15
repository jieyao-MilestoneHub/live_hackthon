"""`crestcut render submit|status` — submit a render and poll it."""
from __future__ import annotations

from .. import views
from ..errors import UsageError
from ..poll import wait_render
from ..upload import download_artifact


def register(subparsers, parent):
    p = subparsers.add_parser("render", parents=[parent], help="submit a render / check status")
    sub = p.add_subparsers(dest="_sub", metavar="<action>")

    sp = sub.add_parser("submit", parents=[parent], help="submit a render for the latest timeline")
    sp.add_argument("project_id", metavar="PROJECT_ID")
    sp.add_argument("--timeline-version", type=int, dest="timeline_version",
                    help="freeze a specific timeline version (default: latest)")
    sp.add_argument("--wait", action="store_true", help="poll until the render finishes")
    sp.add_argument("--timeout", type=float, default=300.0, help="max seconds to wait (default 300)")
    sp.add_argument("--out", metavar="FILE", help="download the finished clip to FILE (implies --wait)")
    sp.set_defaults(_handler=_submit)

    stp = sub.add_parser("status", parents=[parent], help="poll a render's status")
    stp.add_argument("render_id", metavar="RENDER_ID")
    stp.add_argument("--wait", action="store_true", help="poll until terminal")
    stp.add_argument("--timeout", type=float, default=300.0)
    stp.set_defaults(_handler=_status)

    p.set_defaults(_handler=_default)


def _default(ctx, args):
    raise UsageError("specify an action: submit | status")


def _submit(ctx, args):
    created = ctx.api.create_render(args.project_id, args.timeline_version)
    render_id = created["render_id"]
    ctx.printer.success(f"render submitted {render_id} [{created.get('status')}]")

    if not (args.wait or args.out):
        ctx.printer.data(created)
        return

    render = wait_render(ctx.api, render_id, timeout=args.timeout, printer=ctx.printer)
    ctx.printer.success(f"render {render.get('status')}")
    payload = dict(render)
    if args.out and render.get("status") == "SUCCEEDED" and render.get("artifact_id"):
        path = download_artifact(ctx.api, render["artifact_id"], args.out)
        ctx.printer.success(f"downloaded → {path}")
        payload["download_path"] = path
    ctx.printer.data(payload, human=views.render_human)


def _status(ctx, args):
    if args.wait:
        render = wait_render(ctx.api, args.render_id, timeout=args.timeout, printer=ctx.printer)
    else:
        render = ctx.api.get_render(args.render_id)
    ctx.printer.data(render, human=views.render_human)
