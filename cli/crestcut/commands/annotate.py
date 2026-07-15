"""`crestcut annotate` — generate (or read) 5-dimension + narrative-beat annotations."""
from __future__ import annotations


def register(subparsers, parent):
    p = subparsers.add_parser("annotate", parents=[parent],
                              help="generate structured annotations (5 dimensions + beats)")
    p.add_argument("project_id", metavar="PROJECT_ID")
    p.add_argument("--get", action="store_true", help="read existing annotations instead of generating")
    p.set_defaults(_handler=_handle)


def _handle(ctx, args):
    if args.get:
        ctx.printer.data(ctx.api.get_annotations(args.project_id))
        return
    doc = ctx.api.generate_annotations(args.project_id)
    ctx.printer.success(f"{len(doc.get('annotations', []))} annotated highlight(s)")
    ctx.printer.data(doc)
