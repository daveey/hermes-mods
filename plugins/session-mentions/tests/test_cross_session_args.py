"""Regression: `cross_session: target is required for read/send`.

Agents reach cross_session through deferred `tool_call` without loading its schema and guess the
argument name. Every logged failure (3 sessions, Oct 2026) passed the link as `session`:
    {"action": "read", "session": "@session:default/20261014_142820_ff330a"}
The handler must accept the common aliases, and a real miss must say which key to use.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("sm_plugin", Path(__file__).resolve().parent.parent / "__init__.py")
sm = importlib.util.module_from_spec(_spec)
sys.modules["sm_plugin"] = sm  # __init__.py loads as a package; `from . import relay_files` needs it
_spec.loader.exec_module(sm)  # type: ignore[union-attr]


@pytest.fixture
def relay(tmp_path, monkeypatch):
    """Fake Desktop: answer every request immediately and record what was sent."""
    sent = []
    real_write = sm.rf.atomic_write
    for sub in ("outbox", "claimed", "results"):
        (tmp_path / sub).mkdir()
    monkeypatch.setattr(sm.rf, "root", lambda: tmp_path)
    monkeypatch.setattr(sm, "_sender_info", lambda sid: {"session_id": sid or ""})

    def write(path, data):
        real_write(path, data)
        if Path(path).parent.name == "outbox":
            sent.append(data)
            real_write(tmp_path / "results" / f"{data['id']}.json", {"ok": True, "echo": data["args"]})

    monkeypatch.setattr(sm.rf, "atomic_write", write)
    return sent


@pytest.mark.parametrize("args", [
    {"action": "read", "session": "default/20261014_002839_b1c809"},
    {"action": "read", "session": "@session:default/20261014_142820_ff330a"},
    {"action": "read", "session": "@session:default/20261014_205123_3d7c36", "limit": 30},
    {"action": "read", "session_id": "20261014_205123_3d7c36"},
    {"action": "read", "link": "@session:default@mini/abc"},
])
def test_read_accepts_target_aliases(relay, args):
    out = json.loads(sm.handle(args))
    assert out["ok"] is True, out
    alias = next(k for k in args if k not in ("action", "limit"))
    assert relay[-1]["args"]["target"] == args[alias]


def test_send_accepts_target_and_message_aliases(relay):
    out = json.loads(sm.handle({"action": "send", "session": "@session:default/x", "text": "hi"}))
    assert out["ok"] is True, out
    assert relay[-1]["args"]["target"] == "@session:default/x"
    assert relay[-1]["args"]["message"] == "hi"


def test_explicit_target_wins_over_alias(relay):
    sm.handle({"action": "read", "target": "@session:default/real", "session": "other"})
    assert relay[-1]["args"]["target"] == "@session:default/real"


def test_missing_target_error_names_the_key(relay):
    out = json.loads(sm.handle({"action": "read", "sesion": "typo"}))
    assert out["ok"] is False
    assert "target is required" in out["error"]
    assert "`target`" in out["error"] and "sesion" in out["error"]
    assert relay == []
