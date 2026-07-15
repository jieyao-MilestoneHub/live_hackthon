"""The per-invocation Context handed to every command handler.

Kept in its own module so command modules can import it without importing the
root ``cli`` module (avoids a circular import: cli → commands → context).
"""
from __future__ import annotations

from dataclasses import dataclass

from .api import EditorApi
from .config import Config
from .output import Printer
from .transport import Transport


@dataclass
class Context:
    config: Config
    api: EditorApi
    printer: Printer


def build_context(config: Config) -> Context:
    printer = Printer(config.output_mode, verbose=config.verbose)
    transport = Transport(config.api_base, token=config.token, printer=printer)
    return Context(config=config, api=EditorApi(transport), printer=printer)
