"""Every turn that gets no reply check logs why (`reply_skipped`), so a missing reply_checked / nudge event in the
coach log is explained instead of unexplained. Synthetic fixtures only."""
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
ASK = "Why is the nightly export failing?"
REPLY = "The export fails because the temp dir fills up. The cleanup is not fixed."


def _hooks(tmp_path, monkeypatch, name, screen_p=0.1):
    monkeypatch.delenv("COACH_REPLY_RULES", raising=False)
    spec = importlib.util.spec_from_file_location(name, HERE / "__init__.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    core = mod._load("core")
    rcl = mod._load("reply_check")
    monkeypatch.setattr(rcl, "aux_judge", lambda s, p: json.dumps(
        {"handoff": 0, "confirm": 0, "unverified": 0, "unwatched": 0, "report_only": screen_p, "evidence": "screen"}))
    monkeypatch.setattr(rcl, "aux_confirm", lambda s, p: json.dumps(
        {"outcome": "report_only", "agent_fixable": True, "quote": "not fixed"}))
    store = core.Store(root=tmp_path / "coach")
    _, hooks = mod.build(store=store, checkout=None)
    return mod, rcl, store, hooks


def _log(store):
    p = store.root / "log.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def _skips(store):
    return [r for r in _log(store) if r["event"] == "reply_skipped"]


def test_checked_turn_logs_no_skip(tmp_path, monkeypatch):
    _, _, store, hooks = _hooks(tmp_path, monkeypatch, "rs_checked")
    hooks["pre_llm_call"](session_id="a", user_message=ASK, platform="desktop")
    hooks["pre_verify"](session_id="a", platform="desktop", attempt=0, final_response=REPLY)
    hooks["post_llm_call"](session_id="a", assistant_response=REPLY, platform="desktop")
    assert any(r["event"] == "reply_checked" for r in _log(store))
    assert _skips(store) == []


def test_turn_the_host_never_checked_is_logged_with_its_reason(tmp_path, monkeypatch):
    _, _, store, hooks = _hooks(tmp_path, monkeypatch, "rs_unchecked")
    hooks["pre_llm_call"](session_id="b", user_message=ASK, platform="desktop")
    hooks["post_llm_call"](session_id="b", platform="desktop",
                           assistant_response="This turn did not complete. Some actions may already have run.")
    hooks["pre_llm_call"](session_id="b", user_message="and the other one", platform="desktop")
    hooks["post_llm_call"](session_id="b", platform="desktop",
                           assistant_response="Your request was not processed. Send it again.")
    hooks["pre_llm_call"](session_id="b", user_message="try again", platform="desktop")
    hooks["post_llm_call"](session_id="b", platform="desktop", assistant_response=REPLY)
    hooks["post_llm_call"](session_id="b", platform="desktop", assistant_response=REPLY)  # once per turn
    assert [(r["reason"], r["turn"], r["platform"]) for r in _skips(store)] == [
        ("turn_failed", 1, "desktop"), ("turn_failed", 2, "desktop"), ("pre_verify_not_called", 3, "desktop")]


def test_early_returns_in_the_check_log_a_reason(tmp_path, monkeypatch):
    mod, rcl, store, hooks = _hooks(tmp_path, monkeypatch, "rs_early")
    hooks["pre_llm_call"](session_id="c", user_message=ASK, platform="desktop")
    hooks["pre_verify"](session_id="c", platform="desktop", attempt=0, final_response="")
    monkeypatch.setenv("COACH_REPLY_RULES", "none")
    hooks["pre_llm_call"](session_id="c", user_message=ASK, platform="desktop")
    hooks["pre_verify"](session_id="c", platform="desktop", attempt=0, final_response=REPLY)
    monkeypatch.delenv("COACH_REPLY_RULES")

    def boom(*a, **k):
        raise RuntimeError("judge exploded")
    monkeypatch.setattr(rcl, "check", boom)
    hooks["pre_llm_call"](session_id="c", user_message=ASK, platform="desktop")
    assert hooks["pre_verify"](session_id="c", platform="desktop", attempt=0, final_response=REPLY) is None
    reasons = [r["reason"] for r in _skips(store)]
    assert reasons == ["empty_reply", "rules_off", "error:RuntimeError"]
    assert _skips(store)[-1]["detail"] == "judge exploded"


def test_cron_and_continuations_are_not_skips(tmp_path, monkeypatch):
    _, _, store, hooks = _hooks(tmp_path, monkeypatch, "rs_cron")
    hooks["pre_llm_call"](session_id="d", user_message=ASK, platform="cron")
    hooks["pre_verify"](session_id="d", platform="cron", attempt=0, final_response=REPLY)
    hooks["post_llm_call"](session_id="d", platform="cron", assistant_response=REPLY)
    hooks["pre_llm_call"](session_id="e", user_message=ASK, platform="desktop")
    hooks["pre_verify"](session_id="e", platform="desktop", attempt=0, final_response=REPLY)
    hooks["pre_verify"](session_id="e", platform="desktop", attempt=1, final_response=REPLY)
    hooks["post_llm_call"](session_id="e", platform="desktop", assistant_response=REPLY)
    assert _skips(store) == []


def test_report_only_scored_carries_the_platform(tmp_path, monkeypatch):
    _, _, store, hooks = _hooks(tmp_path, monkeypatch, "rs_platform", screen_p=0.65)
    hooks["pre_llm_call"](session_id="f", user_message=ASK, platform="desktop")
    hooks["pre_verify"](session_id="f", platform="desktop", attempt=0, final_response=REPLY)
    scored = [r for r in _log(store) if r["event"] == "report_only_scored"]
    assert scored and all(r["platform"] == "desktop" for r in scored)


def test_a_failed_nudge_outcome_is_logged(tmp_path, monkeypatch):
    _, rcl, store, hooks = _hooks(tmp_path, monkeypatch, "rs_outcome", screen_p=0.65)
    hooks["pre_llm_call"](session_id="g", user_message=ASK, platform="desktop")
    out = hooks["pre_verify"](session_id="g", platform="desktop", attempt=0, final_response=REPLY)
    assert out and out["action"] == "continue"

    def boom(*a, **k):
        raise TimeoutError("judge timed out")
    monkeypatch.setattr(rcl, "nudge_outcome", boom)
    hooks["pre_verify"](session_id="g", platform="desktop", attempt=1, final_response=REPLY + " Filed a card.")
    sk = [r for r in _log(store) if r["event"] == "reply_nudge_outcome_skipped"]
    assert sk and sk[0]["reason"] == "error:TimeoutError"
