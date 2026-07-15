"""`crestcut timeline get` — read a timeline version."""
from __future__ import annotations

from .. import views
from ..errors import UsageError


def register(subparsers, parent):
    p = subparsers.add_parser("timeline", parents=[parent], help="read a project's timeline")
    sub = p.add_subparsers(dest="_sub", metavar="<action>")

    gp = sub.add_parser("get", parents=[parent], help="read a timeline version (latest by default)")
    gp.add_argument("project_id", metavar="PROJECT_ID")
    gp.add_argument("--version", type=int, help="timeline version (default: latest)")
    gp.set_defaults(_handler=_get)

    p.set_defaults(_handler=_default)


def _default(ctx, args):
    raise UsageError("specify an action: get", hint="e.g. `crestcut timeline get PROJECT_ID`")


def _get(ctx, args):
    ctx.printer.data(ctx.api.get_timeline(args.project_id, args.version), human=views.timeline_human)
