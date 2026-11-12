"""Exercise standalone admission without replacing the CLI's lightweight store."""
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "coach_self_heal_cli_store_test", Path(__file__).resolve().parents[1] / "self_heal.py")
sh = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sh
SPEC.loader.exec_module(sh)
FP = "agent-0123456789"
MIDNIGHT = 1794355200.0


@pytest.mark.parametrize("reason", ["daily_cap", "concurrency"])
def test_default_store_cli_denial_logs_once_per_utc_day(tmp_path, monkeypatch, capsys, reason):
    root = tmp_path / "coach"
    monkeypatch.setattr(sh, "coach_root", lambda: root)
    # Replace only the external accounting read, never _store/file_fix/the logger.
    cap = {"available": True, "ok_usd": reason != "daily_cap",
           "ok_running": reason != "concurrency", "why": "daily cap"}
    monkeypatch.setattr(sh, "budget", lambda *a, **kw: cap)
    def no_create(*a, **kw):
        pytest.fail("denied admission must not invoke kanban")
    monkeypatch.setattr(sh, "_run", no_create)
    stamp = [MIDNIGHT - 1]
    monkeypatch.setattr(sh, "time", SimpleNamespace(**{**vars(sh.time), "time": lambda: stamp[0]}))
    lane = sh.LaneState(root)
    lane.update(FP, status="proposed", component="agent", symptom="a test defect")
    for now in (MIDNIGHT - 1, MIDNIGHT - 1, MIDNIGHT, MIDNIGHT + 1):
        stamp[0] = now
        assert sh.main(["fix", FP]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["action"] == "deferred"
        assert result["retry_at"] == (sh._next_utc_midnight(now) if reason == "daily_cap"
                                      else sh._iso(now + 3600))
        assert result["why"] == ("daily cap" if reason == "daily_cap"
                                 else "self-heal concurrency limit reached")
    rows = [json.loads(line) for line in (root / "log.jsonl").read_text().splitlines()]
    assert len(rows) == 2
    assert [r["ts"] for r in rows] == [MIDNIGHT - 1, MIDNIGHT]
    assert all(r["utc_day"] == sh._utc_day(r["ts"]) and r["reason"] == reason for r in rows)
    assert all(r["event"] == "selfheal_fix_deferred" for r in rows)
    assert json.loads((root / "selfheal-deferrals.json").read_text()) == {
        "day": sh._utc_day(MIDNIGHT), "keys": [f"fix:{FP}:{reason}"]}
    assert lane.get(FP)["status"] == "proposed"
