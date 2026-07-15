"""`crestcut describe` — a machine catalog so an AI agent can self-onboard.

Emits the command surface, exit codes, and (unless --no-remote) the live backend
OpenAPI summary. Prefer the live ``GET /openapi.json`` over any checked-in copy so
the catalog can never drift from the running server.
"""
from __future__ import annotations

from .. import __version__
from ..errors import CrestcutError

_EXIT_CODES = {
    "0": "success",
    "2": "usage error",
    "3": "not found / invalid state",
    "4": "auth error",
    "5": "backend unreachable / 5xx",
    "6": "wait timed out",
    "1": "generic error",
}

_GLOBAL_FLAGS = [
    "--profile {local,dev}", "--api-base URL", "--token T",
    "--json", "--plain", "-v/--verbose", "-y/--yes", "--no-input",
]

CATALOG = [
    {"name": "clip", "summary": "one-shot: create→upload→analyze→compose→(render) a clip",
     "usage": "crestcut clip (--chat FILE | --video FILE) [--seconds N] [--render] [--out FILE] [--source-duration-ms MS]"},
    {"name": "up", "summary": "boot a local in-memory backend (uvicorn, zero AWS)",
     "usage": "crestcut up [--port 8080]"},
    {"name": "login", "summary": "log in (Cognito today) and cache a bearer token",
     "usage": "crestcut --profile dev login [--email E] [--password P]"},
    {"name": "logout", "summary": "clear the cached token", "usage": "crestcut logout"},
    {"name": "project create", "summary": "create a project",
     "usage": "crestcut project create [--seconds N] [--source {transcribe,chat}] [--title T]"},
    {"name": "project get", "summary": "show a project's status", "usage": "crestcut project get PROJECT_ID"},
    {"name": "upload", "summary": "upload a video or chat-log CSV",
     "usage": "crestcut upload PROJECT_ID (--video FILE | --chat FILE) [--source-duration-ms MS]"},
    {"name": "analyze", "summary": "run chat-log analysis",
     "usage": "crestcut analyze PROJECT_ID [--source-duration-ms MS] [--video-start-epoch-ms MS]"},
    {"name": "highlights list", "summary": "list detected highlights", "usage": "crestcut highlights list PROJECT_ID"},
    {"name": "highlights patch", "summary": "shift/exclude/lock/select one highlight",
     "usage": "crestcut highlights patch PROJECT_ID HIGHLIGHT_ID [--offset-ms N] [--exclude] [--lock] [--select]"},
    {"name": "compose", "summary": "(re)build the timeline",
     "usage": "crestcut compose PROJECT_ID [--seconds N] [--lock ID...] [--exclude ID...] [--show]"},
    {"name": "timeline get", "summary": "read a timeline version", "usage": "crestcut timeline get PROJECT_ID [--version N]"},
    {"name": "annotate", "summary": "generate/read 5-dimension + beat annotations",
     "usage": "crestcut annotate PROJECT_ID [--get]"},
    {"name": "render submit", "summary": "submit a render (optionally --wait/--out)",
     "usage": "crestcut render submit PROJECT_ID [--wait] [--out FILE] [--timeline-version N]"},
    {"name": "render status", "summary": "poll a render", "usage": "crestcut render status RENDER_ID [--wait]"},
    {"name": "download", "summary": "download a finished artifact", "usage": "crestcut download ARTIFACT_ID --out FILE"},
    {"name": "describe", "summary": "this machine catalog", "usage": "crestcut describe [--json]"},
]


def register(subparsers, parent):
    p = subparsers.add_parser("describe", parents=[parent],
                              help="print a machine catalog of commands (+ live API schema)")
    p.add_argument("--no-remote", action="store_true", dest="no_remote",
                   help="skip fetching the live OpenAPI schema")
    p.set_defaults(_handler=_handle)


def _handle(ctx, args):
    catalog = {
        "tool": "crestcut",
        "version": __version__,
        "profile": ctx.config.profile,
        "api_base": ctx.config.api_base,
        "authenticated": bool(ctx.config.token),
        "exit_codes": _EXIT_CODES,
        "global_flags": _GLOBAL_FLAGS,
        "commands": CATALOG,
    }
    if not args.no_remote:
        try:
            spec = ctx.api.openapi()
            catalog["openapi"] = {
                "version": (spec.get("info") or {}).get("version"),
                "paths": sorted((spec.get("paths") or {}).keys()),
            }
        except CrestcutError as exc:
            catalog["openapi_error"] = exc.message
    ctx.printer.data(catalog, human=_human)


def _human(p, cat):
    print(f"crestcut {cat['version']}  (profile={cat['profile']} → {cat['api_base']})")
    for command in cat["commands"]:
        print(f"  {command['name']:18}  {command['summary']}")
    if cat.get("openapi"):
        print(f"  · live API {cat['openapi']['version']} — {len(cat['openapi']['paths'])} paths")
    elif cat.get("openapi_error"):
        print(f"  · live API unavailable: {cat['openapi_error']}")
