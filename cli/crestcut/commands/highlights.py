"""`crestcut highlights list|patch` — list and correct highlight candidates."""
from __future__ import annotations

from .. import views
from ..errors import UsageError


def register(subparsers, parent):
    p = subparsers.add_parser("highlights", parents=[parent],
                              help="list / correct highlight candidates")
    sub = p.add_subparsers(dest="_sub", metavar="<action>")

    lp = sub.add_parser("list", parents=[parent], help="list detected highlights")
    lp.add_argument("project_id", metavar="PROJECT_ID")
    lp.set_defaults(_handler=_list)

    pp = sub.add_parser("patch", parents=[parent],
                        help="correct one highlight: shift / exclude / lock / select")
    pp.add_argument("project_id", metavar="PROJECT_ID")
    pp.add_argument("highlight_id", metavar="HIGHLIGHT_ID")
    pp.add_argument("--offset-ms", type=int, dest="offset_ms",
                    help="shift the event window vs the chat window (negative = earlier)")
    ex = pp.add_mutually_exclusive_group()
    ex.add_argument("--exclude", dest="exclude", action="store_true", default=None,
                    help="drop this segment (e.g. opener)")
    ex.add_argument("--include", dest="exclude", action="store_false", help="un-exclude")
    lk = pp.add_mutually_exclusive_group()
    lk.add_argument("--lock", dest="locked", action="store_true", default=None, help="lock in place")
    lk.add_argument("--unlock", dest="locked", action="store_false")
    se = pp.add_mutually_exclusive_group()
    se.add_argument("--select", dest="selected", action="store_true", default=None)
    se.add_argument("--deselect", dest="selected", action="store_false")
    pp.add_argument("--note", help="free-text correction note")
    pp.set_defaults(_handler=_patch)

    p.set_defaults(_handler=_default)


def _default(ctx, args):
    raise UsageError("specify an action: list | patch")


def _list(ctx, args):
    ctx.printer.data(
        ctx.api.get_highlights(args.project_id),
        human=views.highlights_human,
        plain=views.highlights_plain,
    )


def _patch(ctx, args):
    body = {}
    if args.offset_ms is not None:
        body["correction_offset_ms"] = args.offset_ms
    if args.exclude is not None:
        body["exclude"] = args.exclude
    if args.locked is not None:
        body["locked"] = args.locked
    if args.selected is not None:
        body["selected"] = args.selected
    if args.note:
        body["note"] = args.note
    if not body:
        raise UsageError("nothing to change",
                         hint="pass one of --offset-ms/--exclude/--lock/--select/--note")
    updated = ctx.api.patch_highlight(args.project_id, args.highlight_id, body)
    ctx.printer.success(f"updated highlight {args.highlight_id}")
    ctx.printer.data(updated)
