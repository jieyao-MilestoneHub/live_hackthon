"""`crestcut project create|get` — create and inspect Projects."""
from __future__ import annotations

from .. import views
from ..errors import UsageError


def register(subparsers, parent):
    p = subparsers.add_parser("project", parents=[parent], help="create / inspect a project")
    sub = p.add_subparsers(dest="_sub", metavar="<action>")

    cp = sub.add_parser("create", parents=[parent], help="create a new project")
    cp.add_argument("--title", help="human title for the project")
    cp.add_argument("--seconds", type=int, default=30,
                    help="target clip length in seconds, 1–60 (default 30)")
    cp.add_argument("--source", choices=["transcribe", "chat"], default="transcribe",
                    help="highlight source: transcribe (video audio) or chat (chat-log)")
    cp.set_defaults(_handler=_create)

    gp = sub.add_parser("get", parents=[parent], help="show a project's status projection")
    gp.add_argument("project_id", metavar="PROJECT_ID")
    gp.set_defaults(_handler=_get)

    p.set_defaults(_handler=_default)


def _default(ctx, args):
    raise UsageError("specify an action: create | get",
                     hint="e.g. `crestcut project create --seconds 30 --source chat`")


def target_ms(seconds: int) -> int:
    ms = int(seconds) * 1000
    if not (1000 <= ms <= 60000):
        raise UsageError("--seconds must be between 1 and 60")
    return ms


def _create(ctx, args):
    body = {"target_duration_ms": target_ms(args.seconds), "analysis_source": args.source}
    if args.title:
        body["title"] = args.title
    proj = ctx.api.create_project(body)
    ctx.printer.success(f"created project {proj['project_id']}")
    ctx.printer.data(proj, human=views.project_human)


def _get(ctx, args):
    ctx.printer.data(ctx.api.get_project(args.project_id), human=views.project_human)
