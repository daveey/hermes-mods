"""A cross_session send asks the Desktop courier to keep re-submitting while another backend holds the target
chat's turn (`retry_busy`; desktop-plugins/session-mentions/plugin.js `busyRetrySeconds`). The tool's wait must
outlast that window and stay under core's cut of a tool call, and when the wait ends the request is marked
cancelled so the courier stops: a send must never report a timeout and then land."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_PLUGIN = Path(__file__).resolve().parents[1]
_JS = _PLUGIN.parents[1] / "desktop-plugins" / "session-mentions" / "plugin.js"
CORE_TOOL_CUT_S = 420  # agent/tool_executor.py _resolve_sequential_tool_timeout: a plugin tool call is cut here
T0 = 1_000_000.0


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
    """time.time()/sleep() for `_relay` and relay_files: a sleep only moves the clock, then runs `on_sleep`."""

    def __init__(self):
        self.now = T0
        self.on_sleep = lambda: None

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        self.on_sleep()


def _run(sm, monkeypatch, action, answer_at=None, result=None, wait=0, claim=True):
    """Run `_relay` on a fake clock. A fake courier claims the request once it is written and, with `answer_at`,
    posts `result` once that many seconds have passed. Returns (tool output, the claimed request, seconds waited,
    the claimed file as it is afterwards)."""
    clock = _Clock()
    base = sm.rf.root()
    for sub in ("outbox", "claimed", "results"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    seen: dict = {}

    def courier():
        for path in (base / "outbox").glob("*.json") if claim else ():
            seen["req"] = json.loads(path.read_text())
            seen["claimed"] = base / "claimed" / path.name
            path.rename(seen["claimed"])
        if "req" in seen and answer_at is not None and clock.now - T0 >= answer_at and "answered" not in seen:
            seen["answered"] = True
            sm.rf.write_result(seen["req"]["id"], result, base)

    clock.on_sleep = courier
    monkeypatch.setattr(sm, "time", clock)
    monkeypatch.setattr(sm.rf, "time", clock)
    monkeypatch.setattr(sm, "_sender_info", lambda _sid: {})
    out = json.loads(sm._relay(action, {"target": "@session:default/abc", "message": "hi"}, "s1", wait))
    after = json.loads(seen["claimed"].read_text()) if seen.get("claimed") and seen["claimed"].exists() else None
    return out, seen.get("req"), clock.now - T0, after


def test_send_request_opts_in_to_the_busy_retry(sm, monkeypatch):
    accepted = {"ok": True, "status": "queued", "queued": True}
    out, req, _, _ = _run(sm, monkeypatch, "send", answer_at=1, result=accepted)
    assert out == accepted
    assert req["retry_busy"] is True and req["retry_busy_seconds"] == sm.SEND_BUSY_RETRY == 300


def test_other_actions_do_not_opt_in(sm, monkeypatch):
    _, req, _, _ = _run(sm, monkeypatch, "read", answer_at=1, result={"ok": True})
    assert "retry_busy" not in req and "retry_busy_seconds" not in req


def test_send_waits_out_the_courier_retry_window(sm, monkeypatch):
    accepted = {"ok": True, "status": "queued", "queued": True}
    out, _, _, after = _run(sm, monkeypatch, "send", answer_at=sm.SEND_BUSY_RETRY + 30, result=accepted)
    assert out == accepted
    assert not (after or {}).get("cancelled")


def test_send_refused_at_the_window_end_is_returned_as_refused_not_as_a_timeout(sm, monkeypatch):
    refused = {"ok": False, "delivered": False, "status": "refused", "retry_safe": True}
    out, _, _, _ = _run(sm, monkeypatch, "send", answer_at=sm.SEND_BUSY_RETRY + 45, result=refused)
    assert out == refused


def test_send_wait_ends_at_window_plus_grace_and_marks_the_request_cancelled(sm, monkeypatch):
    out, req, waited, after = _run(sm, monkeypatch, "send")
    assert out["ok"] is False and "Timed out" in out["error"]
    assert sm.SEND_BUSY_RETRY + sm.SEND_RETRY_GRACE <= waited <= sm.SEND_BUSY_RETRY + sm.SEND_RETRY_GRACE + 1
    assert waited < CORE_TOOL_CUT_S
    assert after["cancelled"] is True and after["id"] == req["id"]
    status = sm.rf.request_status(req["id"])
    assert status["state"] == "claimed" and status["cancelled"] is True
    assert status["age"] == pytest.approx(waited, abs=1)


def test_wait_for_reply_extends_the_wait_as_before(sm, monkeypatch):
    _, _, waited, _ = _run(sm, monkeypatch, "send", wait=120)
    assert sm.SEND_BUSY_RETRY + sm.SEND_RETRY_GRACE + 120 <= waited <= sm.SEND_BUSY_RETRY + sm.SEND_RETRY_GRACE + 121


def test_read_keeps_its_short_wait_and_is_not_marked_cancelled(sm, monkeypatch):
    out, _, waited, after = _run(sm, monkeypatch, "read")
    assert "Timed out" in out["error"]
    assert waited <= sm.PICKUP_TIMEOUT + 121
    assert "cancelled" not in after


@pytest.mark.parametrize("asked, used", [(600, 40), (41, 40), (40, 40), (15, 15), (0, 0)])
def test_reply_wait_is_shortened_so_the_whole_wait_stays_under_the_tool_cut(sm, monkeypatch, asked, used):
    seen = {}

    def relay(action, args, session_id, wait, **_kw):
        seen["wait"] = wait
        return json.dumps({"ok": True, "status": "queued", "reply": None})

    monkeypatch.setattr(sm, "_relay", relay)
    monkeypatch.setattr(sm.rf, "worker_send_refusal", lambda *_a, **_k: None)
    out = json.loads(sm.handle({"action": "send", "target": "@session:default/abc", "message": "hi",
                                "wait_for_reply": asked}, "s1"))
    assert seen["wait"] == used
    assert sm.SEND_BUSY_RETRY + sm.SEND_RETRY_GRACE + seen["wait"] <= sm.SEND_TOTAL_WAIT_MAX == 400 < CORE_TOOL_CUT_S
    if used < asked:
        assert out["reply_wait_seconds"] == used
        assert f"shortened from {asked} s to {used} s" in out["reply_wait_note"]
    else:
        assert "reply_wait_seconds" not in out and "reply_wait_note" not in out
    assert out["ok"] is True and out["status"] == "queued"


def test_shortened_reply_wait_bounds_the_real_wait(sm, monkeypatch):
    """Through the handler and the real `_relay`: the longest a send can block, with the longest reply wait."""
    clock = _Clock()
    base = sm.rf.root()
    for sub in ("outbox", "claimed", "results"):
        (base / sub).mkdir(parents=True, exist_ok=True)

    def courier():
        for path in (base / "outbox").glob("*.json"):
            path.rename(base / "claimed" / path.name)

    clock.on_sleep = courier
    monkeypatch.setattr(sm, "time", clock)
    monkeypatch.setattr(sm.rf, "time", clock)
    monkeypatch.setattr(sm, "_sender_info", lambda _sid: {})
    monkeypatch.setattr(sm.rf, "worker_send_refusal", lambda *_a, **_k: None)
    out = json.loads(sm.handle({"action": "send", "target": "@session:default/abc", "message": "hi",
                                "wait_for_reply": 600}, "s1"))
    assert "Timed out" in out["error"] and out["reply_wait_seconds"] == 40
    assert 400 <= clock.now - T0 <= 401 < CORE_TOOL_CUT_S


def test_interrupted_send_marks_its_claimed_request_cancelled(sm, monkeypatch):
    """Core sets the tool thread's interrupt bit (tools.interrupt) on Stop or when it abandons the call."""
    polls = {"n": 0}

    def interrupted():
        polls["n"] += 1
        return polls["n"] > 4

    monkeypatch.setattr(sm, "_interrupted", interrupted)
    out, req, waited, after = _run(sm, monkeypatch, "send")
    assert out["ok"] is False and "Interrupted while the Desktop was delivering" in out["error"]
    assert waited < 5
    assert after["cancelled"] is True
    assert sm.rf.request_status(req["id"])["cancelled"] is True


def test_interrupted_send_withdraws_an_unclaimed_request(sm, monkeypatch):
    monkeypatch.setattr(sm, "_interrupted", lambda: True)
    out, req, waited, _ = _run(sm, monkeypatch, "send", claim=False)
    assert req is None and waited < 1
    assert "nothing was sent" in out["error"]
    assert not list((sm.rf.root() / "outbox").glob("*.json"))


def test_interrupt_does_not_end_a_read(sm, monkeypatch):
    monkeypatch.setattr(sm, "_interrupted", lambda: True)
    out, _, _, _ = _run(sm, monkeypatch, "read", answer_at=20, result={"ok": True, "messages": []})
    assert out == {"ok": True, "messages": []}


def test_interrupt_signal_is_cores_thread_bit(sm):
    interrupt = pytest.importorskip("tools.interrupt")  # core's module: present in the core venv
    assert sm._interrupted() is False
    interrupt.set_interrupt(True)
    try:
        assert sm._interrupted() is True
    finally:
        interrupt.set_interrupt(False)
    assert sm._interrupted() is False


def test_mark_cancelled_only_flags_a_claimed_request(sm):
    rf = sm.rf
    base = rf.root()
    for sub in ("outbox", "claimed", "results"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    assert rf.mark_cancelled("nope", base) is False
    rf.atomic_write(base / "outbox" / "p1.json", {"id": "p1", "action": "send"})
    assert rf.mark_cancelled("p1", base) is False  # unclaimed: `cancel` withdraws those
    assert rf.request_status("p1", base) == {"state": "pending"}
    rf.atomic_write(base / "claimed" / "c1.json", {"id": "c1", "action": "send"})
    assert rf.request_status("c1", base) == {"state": "claimed"}  # no created_at: no age
    assert rf.mark_cancelled("c1", base) is True
    assert rf.request_status("c1", base) == {"state": "claimed", "cancelled": True}
    assert rf.mark_cancelled(rf.HOME_COURIER_ID, base) is False
    # A result written after the mark keeps it (write_result rewrites the claimed file).
    rf.write_result("c1", {"ok": True}, base)
    assert json.loads((base / "claimed" / "c1.json").read_text())["cancelled"] is True


def _node() -> str | None:
    for cand in (os.environ.get("HERMES_MODS_NODE"), shutil.which("node")):
        if not cand:
            continue
        try:
            major = int(subprocess.run([cand, "-p", "process.versions.node.split('.')[0]"], capture_output=True,
                                       text=True, timeout=20).stdout.strip())
        except Exception:
            continue
        if major >= 22:
            return cand
    return None


_PRINT_WINDOW = """
import fs from 'node:fs/promises'
const source = await fs.readFile(process.argv[2], 'utf8')
globalThis.__smHost = { state: {} }
const code = source.replace("import { COMPOSER_AREAS, host } from '@hermes/plugin-sdk'", 'const host = globalThis.__smHost; const COMPOSER_AREAS = {}')
const m = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'))
console.log(JSON.stringify({ window: m.OWNER_RETRY_WINDOW_S, asked: m.busyRetrySeconds(JSON.parse(process.argv[3])) }))
"""


def test_courier_and_tool_agree_on_the_window(sm, tmp_path):
    """The courier's own cap, read from the loaded module (not from its source text), equals the tool's window, and
    the request the tool writes gets exactly that window from the courier."""
    node = _node()
    if not node:
        pytest.skip("node >= 22 not found (set HERMES_MODS_NODE)")
    script = tmp_path / "window.mjs"
    script.write_text(_PRINT_WINDOW)
    req = {"action": "send", "ttl": 600, "retry_busy": True, "retry_busy_seconds": sm.SEND_BUSY_RETRY}
    run = subprocess.run([node, str(script), str(_JS), json.dumps(req)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr[-2000:]
    got = json.loads(run.stdout)
    assert got == {"window": sm.SEND_BUSY_RETRY, "asked": sm.SEND_BUSY_RETRY}
    assert sm.SEND_BUSY_RETRY + sm.SEND_RETRY_GRACE < CORE_TOOL_CUT_S
