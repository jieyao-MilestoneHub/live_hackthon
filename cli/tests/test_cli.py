"""Unit tests for the crestcut CLI — driven through ``cli.main`` with a fake
transport (no network, no backend). Verifies exit codes, the stdout/stderr split,
the JSON error contract, config precedence, and output modes."""
from __future__ import annotations

import argparse
import json

import pytest

from crestcut import cli, config, context, output
from crestcut.errors import NotFoundError, StateError


class FakeTransport:
    """Stands in for the urllib transport; routes to a responder callable."""

    def __init__(self, responder, **_):
        self._responder = responder
        self.calls: list[tuple] = []

    def request(self, method, path, *, json_body=None, query=None, expect_json=True):
        self.calls.append((method, path, json_body))
        return self._responder(method, path, json_body)


def _run(monkeypatch, responder, argv):
    monkeypatch.setattr(context, "Transport", lambda *a, **k: FakeTransport(responder))
    return cli.main(argv)


# -- exit codes + JSON contract --------------------------------------------
def test_project_get_ok(monkeypatch, capsys):
    def responder(method, path, body):
        assert (method, path) == ("GET", "/projects/p1")
        return {"project_id": "p1", "status": "READY_TO_EDIT", "target_duration_ms": 30000}

    assert _run(monkeypatch, responder, ["project", "get", "p1", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["project_id"] == "p1"


def test_not_found_maps_to_exit_3_with_json_error(monkeypatch, capsys):
    def responder(*_):
        raise NotFoundError("GET /projects/x: project not found")

    assert _run(monkeypatch, responder, ["project", "get", "x", "--json"]) == 3
    cap = capsys.readouterr()
    assert json.loads(cap.out)["error"]["code"] == "NotFoundError"  # stdout: machine
    assert "project not found" in cap.err                           # stderr: human


def test_state_error_maps_to_exit_3(monkeypatch, capsys):
    def responder(*_):
        raise StateError("POST /projects/x/analyze: no chat messages")

    assert _run(monkeypatch, responder, ["analyze", "x", "--json"]) == 3


def test_usage_error_exit_2(monkeypatch, capsys):
    assert _run(monkeypatch, lambda *_: {}, ["project"]) == 2  # no action


def test_describe_no_remote(monkeypatch, capsys):
    assert _run(monkeypatch, lambda *_: {}, ["describe", "--no-remote", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["tool"] == "crestcut"
    assert len(doc["commands"]) >= 12
    assert set(doc["exit_codes"]) >= {"0", "2", "3", "4", "5", "6"}


def test_highlights_list_plain(monkeypatch, capsys):
    def responder(*_):
        return {
            "project_id": "p1",
            "highlights": [
                {"highlight_id": "h1", "score": 0.9, "start_ms": 1000, "end_ms": 4000,
                 "suggested_title": "big laugh"},
            ],
        }

    assert _run(monkeypatch, responder, ["highlights", "list", "p1", "--plain"]) == 0
    out = capsys.readouterr().out
    assert out.split("\t")[0] == "h1"  # tab-separated, id first


# -- config precedence ------------------------------------------------------
def test_flag_beats_env(monkeypatch):
    monkeypatch.setenv("CRESTCUT_API_BASE", "http://from-env")
    ns = argparse.Namespace(api_base="http://from-flag", json=True)
    cfg = config.resolve(ns)
    assert cfg.api_base == "http://from-flag"
    assert cfg.output_mode == "json"


def test_env_beats_default(monkeypatch):
    monkeypatch.setenv("CRESTCUT_API_BASE", "http://from-env")
    cfg = config.resolve(argparse.Namespace())
    assert cfg.api_base == "http://from-env"


def test_profile_dev_default_api_base(monkeypatch):
    monkeypatch.delenv("CRESTCUT_API_BASE", raising=False)
    cfg = config.resolve(argparse.Namespace(profile="dev"))
    assert "execute-api" in cfg.api_base
    assert cfg.profile == "dev"


# -- output printer ---------------------------------------------------------
def test_printer_data_stdout_notes_stderr(capsys):
    p = output.Printer(output.JSON, color=False)
    p.note("progress")
    p.success("ok")
    p.data({"a": 1})
    cap = capsys.readouterr()
    assert json.loads(cap.out) == {"a": 1}   # only data on stdout
    assert "progress" in cap.err and "ok" in cap.err


def test_printer_human_uses_renderer(capsys):
    p = output.Printer(output.HUMAN, color=False)
    p.data({"x": 1}, human=lambda pr, d: print(f"X={d['x']}"))
    assert capsys.readouterr().out.strip() == "X=1"
