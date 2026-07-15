"""crestcut root: argparse tree (command-registry pattern) + dispatch.

Each module in ``crestcut.commands`` owns its own subparser and handler and is
registered here — adding a command is adding one file, never editing a central
switch. Global flags live on a shared parent parser so they work *before or after*
the subcommand (``crestcut --json project get X`` == ``crestcut project get X --json``).
"""
from __future__ import annotations

import argparse
import sys

from . import __version__
from . import config as config_mod
from .commands import COMMANDS
from .context import build_context
from .errors import EXIT_ERROR, EXIT_OK, EXIT_USAGE, CrestcutError
from .output import Printer

_SUPPRESS = argparse.SUPPRESS


def _force_utf8() -> None:
    """Make stdout/stderr UTF-8 so emoji / CJK in payloads never crash the CLI.

    Windows consoles default to the locale codepage (e.g. cp950), which raises
    UnicodeEncodeError on characters outside it. Emitting UTF-8 is correct for
    pipes/files (the JSON path) and, with errors='replace', never crashes a TTY.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def _global_parent() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    g = parent.add_argument_group("global options")
    g.add_argument("--profile", metavar="NAME", default=_SUPPRESS,
                   help="config profile: local (default) or dev")
    g.add_argument("--api-base", dest="api_base", metavar="URL", default=_SUPPRESS,
                   help="backend base URL (overrides the profile)")
    g.add_argument("--token", metavar="TOKEN", default=_SUPPRESS,
                   help="bearer token (else CRESTCUT_TOKEN or `crestcut login`)")
    g.add_argument("--json", action="store_true", default=_SUPPRESS,
                   help="machine-readable JSON on stdout")
    g.add_argument("--plain", action="store_true", default=_SUPPRESS,
                   help="tab-separated, one record per line (grep/awk)")
    g.add_argument("-v", "--verbose", action="store_true", default=_SUPPRESS,
                   help="verbose diagnostics on stderr")
    g.add_argument("-y", "--yes", dest="yes", action="store_true", default=_SUPPRESS,
                   help="assume yes; never prompt")
    g.add_argument("--no-input", dest="no_input", action="store_true", default=_SUPPRESS,
                   help="never prompt (non-interactive / agent mode)")
    return parent


def build_parser() -> argparse.ArgumentParser:
    parent = _global_parent()
    parser = argparse.ArgumentParser(
        prog="crestcut",
        parents=[parent],
        description="crestcut — ride your livestream's crests into clips (浪 LIVE highlight editor).",
        epilog="`crestcut <command> -h` for details · `crestcut describe` for a machine catalog.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"crestcut {__version__}")
    subparsers = parser.add_subparsers(dest="_command", metavar="<command>")
    for module in COMMANDS:
        module.register(subparsers, parent)
    return parser


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    parser = build_parser()
    args = parser.parse_args(argv)

    handler = getattr(args, "_handler", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return EXIT_USAGE

    try:
        cfg = config_mod.resolve(args)
        ctx = build_context(cfg)
    except Exception as exc:  # noqa: BLE001 — config resolution is best-effort
        Printer().error(f"config error: {exc}")
        return EXIT_ERROR

    try:
        handler(ctx, args)
        return EXIT_OK
    except CrestcutError as exc:
        ctx.printer.emit_error(exc.to_dict())
        return exc.exit_code
    except KeyboardInterrupt:
        ctx.printer.error("interrupted")
        return 130
    except BrokenPipeError:
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001 — last-resort guard
        if cfg.verbose:
            raise
        ctx.printer.emit_error(
            {"code": "InternalError", "message": str(exc), "hint": "re-run with -v for a traceback"}
        )
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
