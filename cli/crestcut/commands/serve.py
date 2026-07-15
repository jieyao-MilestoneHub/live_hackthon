"""`crestcut up` — boot a local in-memory backend (uvicorn) for offline use.

Shells out to uvicorn (a *process* boundary, not an import — the CLI stays
decoupled) with the in-memory store + inline render enabled, giving the CLI a
zero-AWS backend to talk to on localhost:8080. Requires the backend-api runtime
deps (fastapi + uvicorn) to be importable in the current environment.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ..errors import BackendError, UsageError


def register(subparsers, parent):
    p = subparsers.add_parser("up", parents=[parent],
                              help="boot a local in-memory backend (uvicorn, zero AWS)")
    p.add_argument("--port", type=int, default=8080,
                   help="port (default 8080 — the stub presigned URLs assume 8080)")
    p.add_argument("--host", default="127.0.0.1", help="bind host (default 127.0.0.1)")
    p.add_argument("--backend-dir", dest="backend_dir",
                   help="path to backend-api (default: auto-detect upward from CWD)")
    p.set_defaults(_handler=_handle)


def _find_backend(start: Path) -> Path | None:
    for base in [start, *start.parents]:
        candidate = base / "backend-api"
        if (candidate / "app" / "main.py").is_file():
            return candidate
    return None


def _handle(ctx, args):
    backend = Path(args.backend_dir) if args.backend_dir else _find_backend(Path.cwd())
    if backend is None or not (backend / "app" / "main.py").is_file():
        raise UsageError("could not locate backend-api",
                         hint="run from inside the repo, or pass --backend-dir /path/to/backend-api")

    if args.port != 8080:
        ctx.printer.warn("stub upload/download URLs assume port 8080; other ports break offline upload")

    env = dict(os.environ)
    env.setdefault("USE_INMEMORY", "1")
    env["RENDER_INLINE_ENCODE"] = "1"  # finish (stub) renders inline so the CLI can download
    env.setdefault("CONTRACTS_DIR", str(backend.parent / "contracts"))
    cmd = [sys.executable, "-m", "uvicorn", "app.main:app",
           "--host", args.host, "--port", str(args.port)]

    ctx.printer.step(f"starting backend at http://{args.host}:{args.port}  (USE_INMEMORY=1, RENDER_INLINE_ENCODE=1)")
    ctx.printer.note(f"  cwd={backend}")
    ctx.printer.note("  talk to it with the default --profile local. Ctrl-C to stop.")
    try:
        code = subprocess.call(cmd, cwd=str(backend), env=env)
    except FileNotFoundError:
        raise UsageError("uvicorn not found in this environment",
                         hint="pip install -r backend-api/requirements.txt (fastapi + uvicorn)")
    if code not in (0, None):
        raise BackendError(f"backend exited with code {code}")
