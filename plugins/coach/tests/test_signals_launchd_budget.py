"""``gateway.launchd_budget_missing`` files its own defect card (gateway-cc28b19bca).

Core writes that exit-diag row when a darwin ``--external-supervisor`` gateway cannot read launchd's live
``ExitTimeOut`` (no job label reached it, or ``launchctl print`` failed) and falls back to assuming the 60 s clamp.
Before, that state was silent: the MacBook's gateway logged "launchd exit timeout None" on every stop for weeks
while launchd restarted into it. The row is a defect at once (not a storm), with its own fingerprint per reason.
"""
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("coach_signals_launchd_budget_test", ROOT / "signals.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.modules.setdefault("coach_signals_launchd_budget_test", mod)
    return mod


sg = _load()
NOW = 1793340000.0


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / "logs").mkdir(parents=True)
    (h / "logs" / "gateway-exit-diag.log").write_text("")
    return h


def _row(home, age=60, **extra):
    row = {"ts": datetime.fromtimestamp(NOW - age, timezone.utc).isoformat(), "tag": "gateway.launchd_budget_missing",
           "level": "warning", "pid": 30673, "assumed_exit_timeout_s": 60, "xpc_service_name": "0", **extra}
    with open(home / "logs" / "gateway-exit-diag.log", "a") as f:
        f.write(json.dumps(row) + "\n")


def test_budget_missing_files_its_own_defect(home):
    _row(home, reason="no_label", label=None)
    sigs = sg.collect_gateway(home, {}, NOW, "macbook")
    assert len(sigs) == 1
    s = sigs[0]
    assert s["kind"] == "gateway" and "launchd" in s["symptom"] and "no_label" in s["symptom"]
    assert s["fp"] == sg.fp("gateway", "macbook", "default", "gateway.launchd_budget_missing", "no_label")
    assert s["fp"] != sg.fp("gateway", "macbook", "default", "gateway.exit_signal", "storm")
    assert not s.get("comment_open_card")


def test_each_boot_of_one_reason_keeps_one_fingerprint_and_reasons_split(home):
    _row(home, reason="no_label", pid=1)
    _row(home, age=30, reason="no_label", pid=2)
    _row(home, age=10, reason="probe_failed", label="ai.hermes.gateway", pid=3)
    fps = [s["fp"] for s in sg.collect_gateway(home, {}, NOW, "macbook")]
    assert len(fps) == 3 and len(set(fps)) == 2
