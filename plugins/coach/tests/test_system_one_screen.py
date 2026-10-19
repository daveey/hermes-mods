"""Jev screens in front of the coach's LLM judges (fix_loop.assess, reply_check.check). No network."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fl = load("fl_s1_test", ROOT / "fix_loop.py")
rc = load("rc_s1_test", ROOT / "reply_check.py")
s1 = load("coach_s1_test", ROOT / "system_one.py")

FIX_JSON = '{"p_failed_fix": 0.95, "issue": "page still broken"}'
STATE = {"last_reply": "Fixed: the page now loads.", "last_log": ["-> terminal(curl ...)"]}


class LLM:
    def __init__(self, reply):
        self.reply, self.calls = reply, 0

    def __call__(self, system, prompt):
        self.calls += 1
        return self.reply


def test_fixloop_low_screen_skips_llm():
    llm, logs = LLM(FIX_JSON), []
    out = fl.assess(dict(STATE), 3, "the page is still not working", llm, threshold=0.8,
                    screen=lambda *a: 0.05, log=logs.append)
    assert out is None and llm.calls == 0 and logs == [{"name": "coach_fixloop", "screen": 0.05, "skipped_llm": True}]


def test_fixloop_high_screen_runs_llm_and_logs_pair():
    llm, logs = LLM(FIX_JSON), []
    out = fl.assess(dict(STATE), 3, "the page is still not working", llm, threshold=0.8,
                    screen=lambda *a: 0.9, log=logs.append)
    assert out and out["n"] == 1 and llm.calls == 1
    assert logs[0]["llm"] == 0.95 and logs[0]["would_skip"] is False


def test_fixloop_screen_unavailable_or_raising_falls_back_to_llm():
    for screen in (lambda *a: None, lambda *a: 1 / 0):
        llm = LLM(FIX_JSON)
        assert fl.assess(dict(STATE), 3, "still broken", llm, threshold=0.8, screen=screen)
        assert llm.calls == 1


def test_fixloop_shadow_never_skips():
    llm, logs = LLM(FIX_JSON), []
    out = fl.assess(dict(STATE), 3, "still broken", llm, threshold=0.8, screen=lambda *a: 0.01,
                    shadow=True, log=logs.append)
    assert out and llm.calls == 1 and logs[0]["would_skip"] is True and logs[0]["shadow"] is True


def test_fixloop_prefilter_still_first():
    calls = []
    assert fl.assess(dict(STATE), 3, "thanks, looks great", LLM(FIX_JSON), screen=lambda *a: calls.append(1)) is None
    assert not calls


CONFIRM = "Done with the refactor. Want me to run the tests?"
VERDICT = '{"handoff": 0.1, "confirm": 0.92, "unverified": 0.1, "unwatched": 0.0, "evidence": "asks to run tests"}'


def test_reply_all_candidates_screened_out_skips_llm():
    llm, logs = LLM(VERDICT), []
    out = rc.check(CONFIRM, "refactor x", [], llm, threshold=0.7, only=["confirm"],
                   screen=lambda cands: {r: 0.05 for r in cands}, log=logs.append)
    assert out is None and llm.calls == 0 and logs[0]["skipped_llm"] is True


def test_reply_surviving_candidate_runs_llm():
    llm = LLM(VERDICT)
    out = rc.check(CONFIRM, "refactor x", [], llm, threshold=0.7, only=["confirm"],
                   screen=lambda cands: {r: 0.6 for r in cands})
    assert out and out["rule"] == "confirm" and llm.calls == 1


def test_reply_screen_only_drops_screened_rules():
    # a candidate the screen doesn't score (not in its dict) is kept
    llm = LLM(VERDICT)
    out = rc.check(CONFIRM, "refactor x", [], llm, threshold=0.7, only=["confirm"], screen=lambda cands: {})
    assert out and llm.calls == 1


def test_reply_shadow_keeps_candidates():
    llm, logs = LLM(VERDICT), []
    out = rc.check(CONFIRM, "refactor x", [], llm, threshold=0.7, only=["confirm"],
                   screen=lambda cands: {r: 0.01 for r in cands}, shadow=True, log=logs.append)
    assert out and llm.calls == 1 and logs[0]["would_drop"] == ["confirm"]


def test_reply_screen_asks_only_backtested_rules(monkeypatch):
    asked = {}

    class Client:
        def mode(self, name, default):
            return "on"

        def decide(self, name, state, questions):
            asked.update(questions)
            return {q: {"type": "noul", "noul": 0.4} for q in questions}

        def p(self, ans, q):
            return ans[q]["noul"]

    monkeypatch.setattr(s1, "client", lambda: Client())
    out = s1.reply_screen(["confirm", "unwatched", "unverified"], "ask", ["log"], "reply", "profile")
    assert out == {"confirm": 0.4} and set(asked) == {"confirm"}
    assert s1.reply_screen(["unwatched"], "ask", [], "reply") is None


def test_mode_off_without_client(monkeypatch):
    monkeypatch.setattr(s1, "client", lambda: None)
    assert s1.mode() == "off" and s1.fixloop_screen("a", [], "b") is None
