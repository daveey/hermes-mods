"""long_wait_pending_reply on a process_manage wait (self-heal kanban-33c561189f).

Incident (hermes-hub session 20261111_200009_edbe39, 2026-11-15): the agent started a card-rehome dry-run in the
background and called process_manage(wait, timeout=300). The rule refused it with "Reply now with what you know", and
the reply said "Running now: the cards those machines created are moving to the hub so nothing gets stranded". The
job finished 14 s later with every move refused (`too_many: ... at most 500 per call`), and the claim was corrected
an hour later. The refusal neither offered the short wait (< MIN_S, which the rule allows) that would have returned
the real result, nor said how to report a job that is still running.
"""
import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(f"coach_lwp_{name}", HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


long_wait = _load("long_wait")
INCIDENT = {"action": "wait", "session_id": "proc_8056948762e8", "timeout": 300}
SID = "20261111_200009_edbe39"


@pytest.fixture
def turns(monkeypatch):
    for k in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_WORKER_TASK"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(long_wait, "persisted_reply", lambda *a, **k: False)
    return {SID: {"turn": 1, "live": True, "delay": False, "replied": False, "held": False}}


def _check(turns, tool, args):
    logs = []
    return long_wait.check(turns, SID, tool, args, logs.append), logs


def test_incident_process_wait_is_still_refused(turns):
    msg, logs = _check(turns, "process_manage", INCIDENT)
    assert msg and logs and logs[0]["rule"] == long_wait.RULE


def test_process_wait_refusal_offers_short_wait_first(turns):
    msg, _ = _check(turns, "process_manage", INCIDENT)
    assert f"timeout under {int(long_wait.MIN_S)}" in msg


def test_process_wait_refusal_says_report_pending_as_pending(turns):
    msg, _ = _check(turns, "process_manage", INCIDENT)
    low = msg.lower()
    assert "still running" in low and "result unknown" in low
    assert "correct" in low  # the next message corrects anything the result contradicts


def test_short_process_wait_passes(turns):
    msg, logs = _check(turns, "process_manage", {**INCIDENT, "timeout": 45})
    assert msg is None and not logs


def test_terminal_sleep_refusal_also_says_pending_is_pending(turns):
    msg, _ = _check(turns, "terminal", {"command": "sleep 135; ssh hub status"})
    assert msg and "still running" in msg.lower() and "result unknown" in msg.lower()
