"""hub-status backend: classification, restart escalation, transition log, window, live status."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("hub_status_core_test", Path(__file__).resolve().parents[1] / "core.py")
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)

UP, RESTARTING, DOWN = core.UP, core.RESTARTING, core.DOWN


# ── test 1: mapping for every gateway_state × restart flag × pid liveness ──────

@pytest.mark.parametrize("gs,restart,alive,want", [
    ("running", False, True, UP),
    ("degraded", False, True, UP),
    ("running", False, False, DOWN),
    ("degraded", True, False, DOWN),
    ("starting", False, False, RESTARTING),
    ("starting", False, True, RESTARTING),
    ("draining", False, True, RESTARTING),
    ("draining", True, False, RESTARTING),
    ("draining", False, False, DOWN),
    ("stopped", True, False, RESTARTING),
    ("stopped", False, False, DOWN),
    ("startup_failed", False, False, DOWN),
    ("startup_failed", True, False, DOWN),
    ("", False, False, DOWN),
])
def test_classify_gateway_states(gs, restart, alive, want):
    state, reason = core.classify({"gateway_state": gs, "restart_requested": restart}, alive)
    assert state == want, reason


def test_classify_restart_exit_reason_and_systemd():
    assert core.classify({"gateway_state": "stopped", "exit_reason": "restart_requested"}, False)[0] == RESTARTING
    assert core.classify({"gateway_state": "stopped", "exit_reason": "sigterm"}, False) == (DOWN, "sigterm")
    for unit in ("activating", "deactivating", "reloading"):
        assert core.classify({"gateway_state": "running"}, False, unit)[0] == RESTARTING
    assert core.classify({"gateway_state": "running"}, True, "active")[0] == UP
    assert core.classify(None, False) == (DOWN, "no gateway state file")


def test_escalate_restart_over_five_minutes_is_down():
    last = {"ts": 1000.0, "state": RESTARTING}
    assert core.escalate(RESTARTING, "draining", last, 1000 + 299) == (RESTARTING, "draining")
    assert core.escalate(RESTARTING, "draining", last, 1000 + 301) == (DOWN, core.STUCK)
    # once escalated it stays down while the restart keeps hanging
    assert core.escalate(RESTARTING, "draining", {"ts": 1301, "state": DOWN, "reason": core.STUCK}, 2000) == (DOWN, core.STUCK)
    # a plain outage turning into a restart is a fresh restart
    assert core.escalate(RESTARTING, "starting", {"ts": 1, "state": DOWN, "reason": "sigterm"}, 9999) == (RESTARTING, "starting")
    assert core.escalate(UP, "running", last, 99999) == (UP, "running")


# ── test 2: transition log ─────────────────────────────────────────────────────

def test_log_writes_only_transitions(tmp_path):
    log = core.EventLog(tmp_path)
    core.observe(log, 100, UP, "running", 7, started=50)
    for t in (110, 120, 130):
        assert core.observe(log, t, UP, "running", 7, started=50) == []
    core.observe(log, 140, RESTARTING, "draining", 7, None)
    core.observe(log, 150, RESTARTING, "starting", None, None)
    core.observe(log, 160, UP, "running", 8, started=155)
    ev = log.read()
    assert [(e["ts"], e["state"], e["pid"]) for e in ev] == [(50, UP, 7), (140, RESTARTING, 7), (155, UP, 8)]


def test_log_records_restart_between_samples(tmp_path):
    log = core.EventLog(tmp_path)
    core.observe(log, 100, UP, "running", 7, started=50)
    added = core.observe(log, 110, UP, "running", 9, started=106)
    assert [(e["state"], e["ts"]) for e in added] == [(RESTARTING, 100), (UP, 106)]


def test_log_records_unwatched_gap_as_down(tmp_path):
    log = core.EventLog(tmp_path)
    core.observe(log, 100, UP, "running", 7, started=50)
    # backend restarted 10 min later; gateway was restarted meanwhile too
    added = core.observe(log, 700, UP, "running", 9, started=650, first=True)
    assert [(e["state"], e["ts"]) for e in added] == [(DOWN, 100), (UP, 650)]
    assert added[0]["reason"] == core.OFFLINE
    # a short gap (< 60 s) is not an outage
    log2 = core.EventLog(tmp_path / "b")
    core.observe(log2, 100, UP, "running", 7, started=50)
    assert core.observe(log2, 130, UP, "running", 7, started=50, first=True) == []


def test_log_escalates_stuck_restart(tmp_path):
    log = core.EventLog(tmp_path)
    core.observe(log, 100, RESTARTING, "draining", 7, None)
    core.observe(log, 350, RESTARTING, "draining", 7, None)
    core.observe(log, 410, RESTARTING, "draining", 7, None)
    core.observe(log, 420, RESTARTING, "draining", 7, None)
    assert [(e["state"], e["ts"]) for e in log.read()] == [(RESTARTING, 100), (DOWN, 410)]


def test_new_restart_after_a_failure_gets_a_fresh_grace(tmp_path):
    log = core.EventLog(tmp_path)
    core.observe(log, 100, RESTARTING, "draining", 7, None)
    core.observe(log, 410, RESTARTING, "draining", 7, None)  # stuck -> down
    core.observe(log, 500, DOWN, "startup_failed", None, None)  # a real failure replaces the stuck latch
    added = core.observe(log, 600, RESTARTING, "starting", None, None)  # operator restarts it
    assert [(e["state"], e["ts"]) for e in added] == [(RESTARTING, 600)]
    # the down spell is still timed from its start, across both logged reasons
    home = tmp_path / "home"
    (home / "hub-status").mkdir(parents=True)
    for e in log.read()[:3]:
        core.EventLog(home / "hub-status").append(e)
    s = core.status(home, now=550, unit_fn=lambda: None)
    assert (s["state"], s["since"]) == (DOWN, 410)


def test_trim_keeps_thirty_days_and_lead_state(tmp_path):
    log = core.EventLog(tmp_path)
    now = 100 * 86400
    for d in (60, 45, 31, 20, 1):
        log.append({"ts": now - d * 86400, "state": UP if d % 2 else DOWN, "reason": "", "pid": d})
    log.append({"ts": "bad", "state": UP})
    log.path.open("a").write("not json\n")
    log.trim(now, force=True)
    assert [e["pid"] for e in log.read()] == [31, 20, 1]


def test_window_leads_with_state_in_force(tmp_path):
    ev = [{"ts": 10, "state": UP}, {"ts": 50, "state": DOWN}, {"ts": 80, "state": UP}]
    w = core.window(ev, 60)
    assert [(e["ts"], e["state"]) for e in w] == [(60, DOWN), (80, UP)]
    assert w[0]["clipped"] is True
    assert core.window(ev, 0) == ev


# ── live status from a fake HERMES_HOME ────────────────────────────────────────

def _home(tmp_path, **rt):
    (tmp_path / "gateway_state.json").write_text(json.dumps(rt))
    return tmp_path


def test_status_up_reports_process_start(tmp_path):
    home = _home(tmp_path, gateway_state="running", pid=os.getpid())
    s = core.status(home, now=10**10, unit_fn=lambda: "active")
    assert s["state"] == UP and s["pid"] == os.getpid()
    assert 0 < s["since"] <= 10**10


def test_status_down_and_restarting_since_last_transition(tmp_path):
    home = _home(tmp_path, gateway_state="running", pid=2**22 + 12345)  # no such pid
    log = core.EventLog(home / "hub-status")
    log.append({"ts": 500.0, "state": DOWN, "reason": "gateway process is gone", "pid": None})
    s = core.status(home, now=800, unit_fn=lambda: None)
    assert (s["state"], s["since"]) == (DOWN, 500.0)
    s = core.status(home, now=800, unit_fn=lambda: "activating")
    assert (s["state"], s["since"]) == (RESTARTING, 800)  # new state not logged yet: starts now


def test_parse_etime():
    assert core._parse_etime("05:07") == 307
    assert core._parse_etime("02:05:07") == 7507
    assert core._parse_etime("3-02:05:07") == 3 * 86400 + 7507
