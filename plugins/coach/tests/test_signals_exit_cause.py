"""A bare ``gateway.exit_nonzero`` fingerprints by its ``cause`` (gateway-3cf2256311).

Core now writes ``cause`` (+ ``exit_reason``) on every False gateway exit. Before, every bare exit_nonzero on one
host/profile shared one fingerprint, so a new cause was filed as a "recurrence" of an already-fixed crash.
Old records with no cause keep their old fingerprint; ``cause: "unrecorded"`` means an exit path with no cause note.
"""
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("coach_signals_exit_cause_test", ROOT / "signals.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.modules.setdefault("coach_signals_exit_cause_test", mod)
    return mod


sg = _load()
NOW = 1793340000.0


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / "logs").mkdir(parents=True)
    (h / "logs" / "gateway-exit-diag.log").write_text("")
    return h


def _exit(home, age=60, **extra):
    row = {"ts": datetime.fromtimestamp(NOW - age, timezone.utc).isoformat(), "tag": "gateway.exit_nonzero",
           "level": "error", "pid": 30673, **extra}
    with open(home / "logs" / "gateway-exit-diag.log", "a") as f:
        f.write(json.dumps(row) + "\n")


def _fps(home):
    return [s["fp"] for s in sg.collect_gateway(home, {}, NOW, "mini")]


def test_two_causes_give_two_fingerprints(home):
    _exit(home, cause="runner_failure", exit_reason="All messaging adapters disconnected")
    _exit(home, age=30, cause="pid_claim_failed")
    fps = _fps(home)
    assert len(fps) == 2 and len(set(fps)) == 2


def test_same_cause_gives_one_fingerprint_even_with_different_pids(home):
    _exit(home, cause="runner_failure", exit_reason="PID 4242 holds the lock")
    _exit(home, age=30, cause="runner_failure", exit_reason="PID 9191 holds the lock")
    assert len(set(_fps(home))) == 1


def test_same_cause_different_reason_class_splits(home):
    _exit(home, cause="runner_failure", exit_reason="All messaging adapters disconnected")
    _exit(home, age=30, cause="runner_failure", exit_reason="Telegram token conflict")
    assert len(set(_fps(home))) == 2


def test_new_cause_differs_from_the_old_bare_fingerprint(home):
    _exit(home)  # an old core: no cause at all keeps the fingerprint already on file
    _exit(home, age=30, cause="runner_failure", exit_reason="All messaging adapters disconnected")
    fps = _fps(home)
    assert fps[0] == sg.fp("gateway", "mini", "default", "gateway.exit_nonzero", "")
    assert fps[1] != fps[0]


def test_unrecorded_files_as_missing_cause_note(home):
    _exit(home, cause="unrecorded")
    sigs = sg.collect_gateway(home, {}, NOW, "mini")
    assert len(sigs) == 1
    assert "exit path missing a cause note" in sigs[0]["symptom"]
    assert sigs[0]["fp"] == sg.fp("gateway", "mini", "default", "gateway.exit_nonzero", "unrecorded")


def test_cause_and_reason_are_in_the_symptom(home):
    _exit(home, cause="runner_failure", exit_reason="All messaging adapters disconnected")
    sym = sg.collect_gateway(home, {}, NOW, "mini")[0]["symptom"]
    assert "runner_failure" in sym and "All messaging adapters disconnected" in sym
