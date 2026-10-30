"""A supervisor wedged behind one live owner is its own defect (gateway-e2cd786685).

One supervised start refused by a live owner is a copy still draining: it goes to the open storm card as a
comment (gateway-cc28b19bca). The SAME owner pid refusing supervised starts for WEDGE_S (launchd retries every
~10 s) means nothing hands the home back: that files its own fingerprint, once, without comment_open_card.
"""
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("coach_signals_wedge_test", ROOT / "signals.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["coach_signals_wedge_test"] = mod
    spec.loader.exec_module(mod)
    return mod


sg = _load()
NOW = 1793340000.0


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / "logs").mkdir(parents=True)
    (h / "logs" / "gateway-exit-diag.log").write_text("")
    return h


def _refusals(home, ages, owner=42853, **extra):
    with open(home / "logs" / "gateway-exit-diag.log", "a") as f:
        for age in ages:
            row = {"ts": datetime.fromtimestamp(NOW - age, timezone.utc).isoformat(), "tag": "gateway.start_refused",
                   "level": "info", "pid": 1000 + int(age), "reason": "already_running", "owner_pid": owner,
                   "owner_alive": True, "external_supervisor": True, **extra}
            f.write(json.dumps(row) + "\n")


def _wedge(sigs):
    return [s for s in sigs if "wedged behind a live owner" in s["symptom"]]


def test_same_owner_refusing_for_ten_minutes_files_a_wedge(home):
    _refusals(home, range(660, -1, -10), handback="planned_stop_requested")  # every 10 s for 11 min
    sigs = sg.collect_gateway(home, {}, NOW, "mini")
    wedge = _wedge(sigs)
    assert len(wedge) == 1, [s["symptom"] for s in sigs]
    assert wedge[0]["fp"] == sg.fp("gateway", "mini", "default", "gateway.start_refused", "wedged-owner")
    assert "owner_pid=42853" in wedge[0]["symptom"] and "handback planned_stop_requested" in wedge[0]["symptom"]
    assert not wedge[0].get("comment_open_card")
    # The per-refusal storm comments are unchanged.
    assert sum(1 for s in sigs if s.get("comment_open_card")) == 67


def test_a_drain_that_ends_within_the_window_is_no_wedge(home):
    """The handback working: the owner refuses for ~2 min while it drains, then the supervisor wins."""
    _refusals(home, range(120, -1, -10))
    assert _wedge(sg.collect_gateway(home, {}, NOW, "mini")) == []


def test_different_owners_never_add_up(home):
    for i, age in enumerate(range(660, -1, -60)):
        _refusals(home, [age], owner=50000 + i)
    assert _wedge(sg.collect_gateway(home, {}, NOW, "mini")) == []


def test_wedge_state_survives_passes_through_the_cursor(home):
    cur = {}
    _refusals(home, range(660, 300, -10))
    assert _wedge(sg.collect_gateway(home, cur, NOW - 300, "mini")) == []
    assert cur["gw_wedge"]  # carried to the next pass
    _refusals(home, range(300, -1, -10))
    assert len(_wedge(sg.collect_gateway(home, cur, NOW, "mini"))) == 1
