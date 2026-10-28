"""self-heal: `event fixed` on a cron-* fingerprint is refused while that job is still failing.

Replay of cron-5aa82ca809 (bsky-notify on hermes-hub, 2026-10-28): the fix card recorded `fixed` at 04:48Z while
the job's failure_streak was 7 and climbing, so the lane closed a defect that was still live and the same failure
only came back as a recurrence hours later. The guard reads the job's live failure_streak from <home>/cron/jobs.json
and records `selfheal_fix_pending` instead, keeping the lane open until a run succeeds.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.modules[name] = mod
    return mod


sh = load("coach_self_heal_cron_guard_test", ROOT / "self_heal.py")
core = load("coach_core_cron_guard_test", ROOT / "core.py")
signals = load("coach_signals_cron_guard_test", ROOT / "signals.py")

NOW = 1793170000.0
OPEN = lambda board=None, now=None: {"usd": 1.0, "cap": 25.0, "running": 0, "ok_usd": True, "ok_running": True,
                                     "why": ""}


class Runner:
    def __init__(self):
        self.n = 0

    def __call__(self, argv, capture_output=True, text=True, input=None, timeout=None):
        from types import SimpleNamespace
        if "create" in argv:
            self.n += 1
            return SimpleNamespace(returncode=0, stdout=f"Created t_0000000{self.n}\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")


def ev(store, name):
    p = Path(store.root) / "log.jsonl"
    return [r for r in (json.loads(x) for x in p.read_text().splitlines())
            if r.get("event") == name] if p.exists() else []


def write_job(home: Path, streak: int, threshold: int = 3):
    (home / "cron").mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(f"cron:\n  failure_nudge_threshold: {threshold}\n")
    (home / "cron" / "jobs.json").write_text(json.dumps({"jobs": [
        {"id": "5ab5f1a9aa97", "name": "bsky-notify", "failure_streak": streak,
         "last_status": "error" if streak else "ok"}]}))


@pytest.fixture
def lane(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    store = core.Store(tmp_path / "coach")
    host = sh.host_name()
    fp = signals.fp("cron", host, "default", "bsky-notify")
    d = {"component": "cron", "symptom": f"cron job 'bsky-notify' failed on {host}: Script exited with code 1",
         "source": "signal", "kind": "hermes_bug", "p_defect": 0.9, "session_id": None, "host": host,
         "at": NOW - 60, "fingerprint": fp}
    r = Runner()
    sh.file_defect(d, store=store, m="live", runner=r, now=NOW, cfg={}, budget_fn=OPEN)
    sh.record_event("proposed", fp, "t_00000001", store=store, now=NOW + 10)
    sh.file_fix(fp, store=store, runner=r, now=NOW + 20, budget_fn=OPEN)
    return home, store, fp


def test_fixed_refused_while_job_still_failing(lane):
    home, store, fp = lane
    write_job(home, streak=7)
    with pytest.raises(sh.FixPending):
        sh.record_event("fixed", fp, "t_00000002", store=store, now=NOW + 30)
    e = sh.LaneState(store.root).get(fp)
    assert e["status"] == "approved"  # lane stays open: the fix card is still the owner
    assert not e.get("fixed_at")
    (rec,) = ev(store, "selfheal_fix_pending")
    assert rec["fingerprint"] == fp and rec["failure_streak"] == 7 and rec["threshold"] == 3
    assert ev(store, "selfheal_fixed") == []


def test_fixed_recorded_once_the_job_recovered(lane):
    home, store, fp = lane
    write_job(home, streak=0)
    e = sh.record_event("fixed", fp, "t_00000002", store=store, now=NOW + 30)
    assert e["status"] == "fixed" and e["fixed_at"] == NOW + 30
    assert ev(store, "selfheal_fix_pending") == []


def test_streak_below_threshold_counts_as_recovering(lane):
    home, store, fp = lane
    write_job(home, streak=2, threshold=3)
    assert sh.record_event("fixed", fp, "t_00000002", store=store, now=NOW + 30)["status"] == "fixed"


def test_unreadable_job_does_not_block(lane):
    # the job lives on another host (or jobs.json is gone): the guard cannot judge, so it records fixed and says so
    home, store, fp = lane
    e = sh.record_event("fixed", fp, "t_00000002", store=store, now=NOW + 30)
    assert e["status"] == "fixed"
    (rec,) = ev(store, "selfheal_fixed")
    assert rec.get("cron_check") == "unchecked"


def test_cli_exits_nonzero_when_pending(lane, monkeypatch, capsys):
    home, store, fp = lane
    write_job(home, streak=5)
    monkeypatch.setattr(sh, "_store", lambda store_=None: store)
    with pytest.raises(SystemExit) as ex:
        sh.main(["event", "fixed", fp, "--card", "t_00000002", "--note", "abc123"])
    assert ex.value.code == 3
    assert "fix_pending" in capsys.readouterr().out
