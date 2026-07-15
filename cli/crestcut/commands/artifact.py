"""`crestcut download` — write a finished artifact (clip) to a local file."""
from __future__ import annotations

from ..upload import download_artifact


def register(subparsers, parent):
    p = subparsers.add_parser("download", parents=[parent],
                              help="download a finished artifact (clip) to a file")
    p.add_argument("artifact_id", metavar="ARTIFACT_ID")
    p.add_argument("--out", metavar="FILE", required=True, help="output file path")
    p.set_defaults(_handler=_handle)


def _handle(ctx, args):
    path = download_artifact(ctx.api, args.artifact_id, args.out)
    ctx.printer.success(f"downloaded → {path}")
    ctx.printer.data({"artifact_id": args.artifact_id, "download_path": path})
