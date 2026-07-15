"""Configuration resolution.

Precedence (12-Factor CLI): **flags › env (``CRESTCUT_*``) › project
``./.crestcut.toml`` › user config › built-in defaults**. A ``--profile`` selects
an environment (``local`` = a backend on localhost; ``dev`` = the deployed AWS
API). The backend is a *backing service addressed by a URL* — swapping clouds is
a profile change, not a code change.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import output

# Built-in profiles. `dev` points at the checked-in deployed API (CLAUDE.md);
# override any of this via ~/.config/crestcut/config.toml or ./.crestcut.toml.
DEFAULT_PROFILES: dict[str, dict[str, Any]] = {
    "local": {
        "api_base": "http://localhost:8080",
    },
    "dev": {
        "api_base": "https://3xgcvbiz3j.execute-api.us-east-1.amazonaws.com",
        "cognito_region": "us-east-1",
        "cognito_client_id": "",
    },
}
DEFAULT_PROFILE = "local"


@dataclass(frozen=True)
class Config:
    profile: str
    api_base: str
    token: str | None
    output_mode: str
    verbose: bool
    assume_yes: bool
    no_input: bool
    cognito_region: str | None
    cognito_client_id: str | None


def config_dir() -> Path:
    """User config/cache dir (override with ``CRESTCUT_CONFIG_DIR``)."""
    override = os.environ.get("CRESTCUT_CONFIG_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return Path(base) / "crestcut"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path(os.path.expanduser("~")) / ".config"
    return base / "crestcut"


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _file_config() -> dict[str, Any]:
    """Merge user config then project config (project overrides user)."""
    merged: dict[str, Any] = {}
    for path in (config_dir() / "config.toml", Path.cwd() / ".crestcut.toml"):
        data = _read_toml(path)
        if data:
            _deep_update(merged, data)
    return merged


def _deep_update(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for key, val in src.items():
        if isinstance(val, dict) and isinstance(dst.get(key), dict):
            _deep_update(dst[key], val)
        else:
            dst[key] = val


def _first(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _token_cache_path(profile: str) -> Path:
    return config_dir() / f"token-{profile}"


def read_cached_token(profile: str) -> str | None:
    try:
        text = _token_cache_path(profile).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def write_cached_token(profile: str, token: str) -> Path:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = _token_cache_path(profile)
    path.write_text(token, encoding="utf-8")
    try:  # best-effort chmod 0600 (POSIX; a no-op-ish on Windows)
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def clear_cached_token(profile: str) -> bool:
    try:
        _token_cache_path(profile).unlink()
        return True
    except OSError:
        return False


def resolve(args: Any) -> Config:
    """Fold flags + env + files + defaults into a single Config."""
    file_cfg = _file_config()
    file_profiles: dict[str, Any] = file_cfg.get("profiles", {}) or {}

    profile = _first(
        getattr(args, "profile", None),
        os.environ.get("CRESTCUT_PROFILE"),
        file_cfg.get("profile"),
        DEFAULT_PROFILE,
    )
    prof = {
        **DEFAULT_PROFILES.get(profile, {}),
        **(file_profiles.get(profile, {}) or {}),
    }

    api_base = _first(
        getattr(args, "api_base", None),
        os.environ.get("CRESTCUT_API_BASE"),
        prof.get("api_base"),
        DEFAULT_PROFILES["local"]["api_base"],
    )
    api_base = str(api_base).rstrip("/")

    token = _first(
        getattr(args, "token", None),
        os.environ.get("CRESTCUT_TOKEN"),
        read_cached_token(profile),
    )

    if getattr(args, "json", False):
        mode = output.JSON
    elif getattr(args, "plain", False):
        mode = output.PLAIN
    elif os.environ.get("CRESTCUT_JSON"):
        mode = output.JSON
    else:
        mode = output.HUMAN

    return Config(
        profile=profile,
        api_base=api_base,
        token=token,
        output_mode=mode,
        verbose=bool(getattr(args, "verbose", False)),
        assume_yes=bool(getattr(args, "yes", False)),
        no_input=bool(getattr(args, "no_input", False)),
        cognito_region=_first(
            os.environ.get("CRESTCUT_COGNITO_REGION"), prof.get("cognito_region")
        ),
        cognito_client_id=_first(
            os.environ.get("CRESTCUT_COGNITO_CLIENT_ID"), prof.get("cognito_client_id")
        ),
    )
