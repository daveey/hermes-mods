"""The end-of-turn reply check runs after the answer streamed, so its time is time the user waits on a finished-looking
answer (self-heal agent-7aa110cfd7: a confirm pass kept a Desktop turn spinning 23 s). `report_only_scored` and
`reply_checked` carry `elapsed_s` (seconds since the pre_verify budget started) so the lane can see which judge is slow."""
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"t_elapsed_{name}", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rc = _mod("reply_check")


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_report_only_scored_carries_elapsed_s():
    clock = Clock()
    budget = rc.Budget(30.0, host_timeout_s=None, clock=clock)
    v = {r: 0.0 for r in rc.RULES}
    v.update(report_only=0.8)

    def screen(*a):
        clock.t += 4.0
        return json.dumps(v)

    def confirm():
        clock.t += 19.0
        return (0.1, "a fix is running")

    events = []
    token = rc.use_budget(budget)
    try:
        rc.check("The cache is still broken.", "Fix it.", [], screen, only=["report_only"], audit=events.append,
                 confirm=confirm, confirm_floor=0.6)
    finally:
        rc.reset_budget(token)
    e = next(e for e in events if e["event"] == "report_only_scored")
    assert e["confirm_attempted"] is True
    assert e["elapsed_s"] == 23.0


def test_elapsed_absent_outside_pre_verify():
    assert rc.budget_elapsed() is None
    events = []
    v = {r: 0.0 for r in rc.RULES}
    v.update(report_only=0.35)
    rc.check("The cache is still broken.", "Fix it.", [], lambda *a: json.dumps(v), only=["report_only"],
             audit=events.append)
    e = next(e for e in events if e["event"] == "report_only_scored")
    assert "elapsed_s" not in e


def test_reply_checked_carries_elapsed_s(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_REPLY_RULES", "report_only")
    spec = importlib.util.spec_from_file_location("coach_init_elapsed", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    core = mod._load("core")
    rcl = mod._load("reply_check")
    monkeypatch.setattr(rcl, "aux_judge", lambda s, p: json.dumps(
        {"handoff": 0, "confirm": 0, "unverified": 0, "unwatched": 0, "report_only": 0.1, "evidence": "e"}))
    store = core.Store(root=tmp_path / "coach")
    _, hooks = mod.build(store=store, checkout=None)
    sid = "el1"
    hooks["pre_llm_call"](session_id=sid, user_message="Fix the cache.", platform="tui")
    hooks["pre_verify"](session_id=sid, platform="tui", attempt=0,
                        final_response="The cache is still broken; the refresh job is not deployed.")
    logged = [json.loads(l) for l in (tmp_path / "coach" / "log.jsonl").read_text().splitlines()]
    rec = [r for r in logged if r["event"] == "reply_checked" and r["session_id"] == sid]
    assert rec, logged
    assert isinstance(rec[0].get("elapsed_s"), float) and rec[0]["elapsed_s"] >= 0
