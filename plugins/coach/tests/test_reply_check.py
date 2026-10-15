import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"t_{name}", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rc = _mod("reply_check")


def test_prefilter_catches_each_pattern_and_skips_plain_reports():
    assert "handoff" in rc.prefilter("Open System Settings and click Allow, then let me know when it's done.")
    assert "handoff" in rc.prefilter("1. Click **Install to Workspace**\n2. Copy the token")
    assert "confirm" in rc.prefilter("The fix is ready on a branch.\n\nWant me to run the tests and merge it?")
    assert "unverified" in rc.prefilter("Fixed. The sidebar now scrolls.")
    assert rc.prefilter("Here are the three options I found, with their trade-offs.") == []


def _judge(verdict):
    calls = []

    def llm(system, prompt):
        calls.append(prompt)
        return json.dumps(verdict)
    return llm, calls


def test_check_fires_only_over_threshold_and_on_prefiltered_rules():
    llm, calls = _judge({"handoff": 0.95, "confirm": 0.1, "unverified": 0.9, "evidence": "asks user to click Allow"})
    hit = rc.check("Click Allow, then let me know when you've done it.", "set up slack", ["-> terminal(ls)"], llm)
    assert hit["rule"] == "handoff" and "self-serve" in hit["nudge"] and "click Allow" in hit["nudge"]
    # unverified scored high but wasn't a prefilter candidate for this reply -> not chosen
    assert len(calls) == 1
    llm2, calls2 = _judge({"handoff": 0.5, "confirm": 0.1, "unverified": 0.2, "evidence": ""})
    assert rc.check("Click Allow, then let me know when you've done it.", "x", [], llm2) is None
    llm3, calls3 = _judge({"handoff": 1, "confirm": 1, "unverified": 1, "evidence": ""})
    assert rc.check("Here is the summary you asked for.", "x", [], llm3) is None and calls3 == []  # no LLM call


def test_check_respects_rule_allowlist_and_bad_judge_output():
    llm, _ = _judge({"handoff": 0.99, "confirm": 0.99, "unverified": 0.0, "evidence": "e"})
    reply = "Open Settings and click Allow, then tell me when it's done. Want me to restart it after?"
    assert rc.check(reply, "x", [], llm, only=["confirm"])["rule"] == "confirm"
    assert rc.check(reply, "x", [], lambda s, p: "not json") is None


def test_settings_env(monkeypatch):
    monkeypatch.setenv("COACH_REPLY_RULES", "handoff,bogus")
    monkeypatch.setenv("COACH_REPLY_THRESHOLD", "0.9")
    assert rc.settings() == {"rules": ["handoff"], "threshold": 0.9}
    monkeypatch.setenv("COACH_REPLY_RULES", "none")
    assert rc.settings()["rules"] == []


def test_pre_verify_hook_nudges_once_per_turn(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("coach_init_rc", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    core = mod._load("core")
    rcl = mod._load("reply_check")
    monkeypatch.setattr(rcl, "aux_judge", lambda s, p: json.dumps(
        {"handoff": 0.0, "confirm": 0.92, "unverified": 0.0, "evidence": "asks to run tests"}))
    store = core.Store(root=tmp_path / "coach")
    coach, hooks = mod.build(store=store, checkout=None)
    hooks["pre_llm_call"](session_id="s1", user_message="fix the flaky test")
    hooks["post_tool_call"](tool_name="terminal", args={"command": "pytest -x"}, result="{}", session_id="s1")
    reply = "I found the race and patched it.\n\nWant me to run the full suite now?"
    out = hooks["pre_verify"](session_id="s1", attempt=0, final_response=reply, changed_paths=["a.py"])
    assert out["action"] == "continue" and "proceed on reversible work" in out["message"]
    assert hooks["pre_verify"](session_id="s1", attempt=1, final_response=reply) is None
    assert hooks["pre_verify"](session_id="s1", attempt=0, final_response=reply, platform="cron") is None
    logged = [json.loads(l) for l in (tmp_path / "coach" / "log.jsonl").read_text().splitlines()]
    assert any(r["event"] == "reply_nudge" and r["rule"] == "confirm" for r in logged)
    ask, tlog = coach.turn_context("s1")
    assert ask == "fix the flaky test" and tlog and "pytest -x" in tlog[0]


def test_settings_defaults(monkeypatch):
    monkeypatch.delenv("COACH_REPLY_RULES", raising=False)
    monkeypatch.delenv("COACH_REPLY_THRESHOLD", raising=False)
    assert rc.settings() == {"rules": ["handoff", "confirm", "unwatched"], "threshold": 0.7}


ORPHAN = ("Everything is running.\n\nOnce the last runs finish, around 03:30 UTC, I'll build the tables from the "
          "result files, write the report and merge the PR.")


def test_prefilter_unwatched_promises():
    assert "unwatched" in rc.prefilter(ORPHAN)
    assert "unwatched" in rc.prefilter("Smoke test is green. I'll launch the sweep as soon as CI passes.")
    assert "unwatched" in rc.prefilter("Nothing else to do here; waiting on the eval runs.")
    assert "unwatched" not in rc.prefilter("The runs finished and the tables are below.")


def test_unwatched_needs_no_wake_source_before_the_judge_runs():
    verdict = {"handoff": 0, "confirm": 0, "unverified": 0, "unwatched": 0.9, "evidence": "promises tables at 03:30"}
    llm, calls = _judge(verdict)
    assert rc.check(ORPHAN, "run it", [], llm, wake=lambda: ["process:proc_1"]) is None and calls == []
    assert rc.check(ORPHAN, "run it", [], llm) is None and calls == []  # can't tell -> skip
    assert rc.check(ORPHAN, "run it", [], llm, wake=lambda: 1 / 0) is None and calls == []  # lookup error -> skip
    hit = rc.check(ORPHAN, "run it", [], llm, wake=lambda: [])
    assert hit["rule"] == "unwatched" and "background=true, notify=true" in hit["nudge"] and len(calls) == 1
    low, _ = _judge({**verdict, "unwatched": 0.3})
    assert rc.check(ORPHAN, "run it", [], low, wake=lambda: []) is None


def test_wake_sources_tool_log_and_live_process(tmp_path, monkeypatch):
    import os
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(rc, "_hermes_home", lambda: tmp_path)
    assert rc.wake_sources("s1", ["-> terminal(ls)"]) == []
    assert rc.wake_sources("s1", ["-> delegate_task(train it)"]) == ["tool:delegate_task"]
    (tmp_path / "processes.json").write_text(json.dumps([
        {"session_id": "proc_live", "session_key": "s1", "pid": os.getpid()},
        {"session_id": "proc_other", "session_key": "s2", "pid": os.getpid()},
        {"session_id": "proc_dead", "session_key": "s1", "pid": 2 ** 22 + 12345}]))
    import sys
    monkeypatch.setitem(sys.modules, "tools.process_registry", None)  # force the processes.json fallback
    assert rc.wake_sources("s1", []) == ["process:proc_live"]
    assert rc.wake_sources("s3", []) == []
