"""``python -m crestcut`` entry point (mirrors the ``crestcut`` console script)."""
from __future__ import annotations

from crestcut.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
