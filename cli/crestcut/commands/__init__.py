"""Command registry. Each module exposes ``register(subparsers, parent)`` and its
own handler(s); adding a command is adding a module + one line here. Order sets
how commands appear in ``--help`` (headline ``clip`` first)."""
from __future__ import annotations

from . import (
    analyze,
    annotate,
    artifact,
    compose,
    describe,
    flow,
    highlights,
    login,
    project,
    render,
    serve,
    timeline,
    upload,
)

COMMANDS = [
    flow,        # clip (headline one-shot)
    project,
    upload,
    analyze,
    highlights,
    compose,
    timeline,
    annotate,
    render,
    artifact,    # download
    login,       # login + logout
    serve,       # up
    describe,
]
