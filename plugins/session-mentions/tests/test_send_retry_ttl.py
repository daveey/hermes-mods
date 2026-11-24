"""The Desktop courier re-submits a send that core refuses (another backend holds the chat's turn) until the
request's TTL runs out (desktop-plugins/session-mentions/plugin.js `sendRetryUntil`). The tool's wait for the
answer must outlast that window, or a send would report a timeout and then land anyway."""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

_PLUGIN = Path(__file__).resolve().parents[1]
_JS = _PLUGIN.parents[1] / "desktop-plugins" / "session-mentions" / "plugin.js"


@pytest.fixture()
def sm(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    spec = importlib.util.spec_from_file_location("sm_send_retry_ttl", _PLUGIN / "__init__.py",
                                                  submodule_search_locations=[str(_PLUGIN)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop(spec.name, None)


class _Clock:
    """time.time()/sleep() for `_relay`: a sleep only moves the clock."""

    def __init__(self):
        self.now = 1_000_000.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _claim_when_written(sm, base, answer_at, clock, result):
    """Play the courier: claim the request as soon as it is in the outbox, answer once the clock passes `answer_at`."""
    seen = {}
    real_sleep = clock.sleep

    def sleep(seconds):
        real_sleep(seconds)
        for path in (base / "outbox").glob("*.json"):
            seen["req"] = json.loads(path.read_text())
            path.rename(base / "claimed" / path.name)
        if "req" in seen and clock.now - 1_000_000.0 >= answer_at and "answered" not in seen:
            seen["answered"] = clock.now
            sm.rf.write_result(seen["req"]["id"], result, base)
    clock.sleep = sleep
    return seen


def _run(sm, monkeypatch, action, answer_at, result, wait=0):
    clock = _Clock()
    base = sm.rf.root()
    for sub in ("outbox", "claimed", "results"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    seen = _claim_when_written(sm, base, answer_at, clock, result)
    monkeypatch.setattr(sm, "time", clock)
    monkeypatch.setattr(sm, "_sender_info", lambda _sid: {})
    out = json.loads(sm._relay(action, {"target": "@session:default/abc", "message": "hi"}, "s1", wait))
    return out, seen, clock.now - 1_000_000.0


def test_send_waits_out_the_courier_retry_window(sm, monkeypatch):
    accepted = {"ok": True, "status": "queued", "queued": True}
    out, seen, _ = _run(sm, monkeypatch, "send", answer_at=sm.SEND_TTL + 30, result=accepted)
    assert out == accepted
    assert seen["req"]["ttl"] == sm.SEND_TTL


def test_send_refused_at_ttl_is_returned_as_refused_not_as_a_timeout(sm, monkeypatch):
    refused = {"ok": False, "delivered": False, "status": "refused", "retry_safe": True}
    out, _, _ = _run(sm, monkeypatch, "send", answer_at=sm.SEND_TTL + 60, result=refused)
    assert out == refused


def test_send_wait_is_bounded(sm, monkeypatch):
    out, _, waited = _run(sm, monkeypatch, "send", answer_at=10 ** 9, result={})
    assert out["ok"] is False and "Timed out" in out["error"]
    assert sm.SEND_TTL + sm.SEND_RETRY_GRACE <= waited <= sm.SEND_TTL + sm.SEND_RETRY_GRACE + 2


def test_read_keeps_its_short_wait(sm, monkeypatch):
    out, _, waited = _run(sm, monkeypatch, "read", answer_at=10 ** 9, result={})
    assert "Timed out" in out["error"]
    assert waited <= sm.PICKUP_TIMEOUT + 122


def test_courier_and_tool_agree_on_the_ttl(sm):
    js = _JS.read_text()
    cap = re.search(r"export function sendRetryUntil[\s\S]*?Math\.min\((\d+), Math\.max\(0, Number\(req\.ttl\)", js)
    assert cap, "sendRetryUntil no longer caps the request TTL where this test reads it"
    assert int(cap.group(1)) == sm.SEND_TTL
    # The last retry starts at the deadline at the latest and its submit times out after 60 s.
    assert sm.SEND_RETRY_GRACE > 60
