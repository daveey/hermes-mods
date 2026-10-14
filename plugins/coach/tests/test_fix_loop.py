import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("t_fix_loop", HERE / "fix_loop.py")
fl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fl)


def judge(p, issue="sidebar scroll"):
    calls = []

    def llm(system, prompt):
        calls.append(prompt)
        return json.dumps({"p_failed_fix": p, "issue": issue})
    return llm, calls


def test_prefilter():
    assert fl.prefilter("still doesn't scroll")
    assert fl.prefilter("[image]")
    assert fl.prefilter("i don't see it")
    assert not fl.prefilter("great, now add a dark mode toggle")


def test_assess_escalates_within_window_and_resets_after():
    st = {"last_reply": "Fixed: the sidebar scrolls now.", "fails": []}
    llm, calls = judge(0.95)
    a = fl.assess(st, 1, "still doesn't scroll", llm)
    assert a["n"] == 1 and "reproduce it on the user's" in a["hint"] and "sidebar scroll" in a["hint"]
    b = fl.assess(st, 3, "same thing, no change", llm)
    assert b["n"] == 2 and "Stop iterating" in b["hint"]
    c = fl.assess(st, 3 + fl.WINDOW_TURNS + 1, "still broken", llm)
    assert c["n"] == 1  # old failures aged out


def test_assess_skips_without_prior_reply_prefilter_or_confidence():
    llm, calls = judge(0.95)
    assert fl.assess({"fails": []}, 1, "still broken", llm) is None
    assert fl.assess({"last_reply": "x", "fails": []}, 1, "thanks, looks good", llm) is None
    assert calls == []
    low, _ = judge(0.5)
    assert fl.assess({"last_reply": "Fixed.", "fails": []}, 1, "still broken", low) is None
    assert fl.assess({"last_reply": "Fixed.", "fails": []}, 1, "still broken", lambda s, p: "garbage") is None


def test_hooks_end_to_end(tmp_path, monkeypatch):
    ispec = importlib.util.spec_from_file_location("coach_init_fl", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(ispec)
    ispec.loader.exec_module(mod)
    core = mod._load("core")
    flm = mod._load("fix_loop")
    monkeypatch.setattr(flm, "aux_judge", lambda s, p: json.dumps({"p_failed_fix": 0.9, "issue": "login 500"}))
    store = core.Store(root=tmp_path / "coach")
    coach, hooks = mod.build(store=store, checkout=None)
    assert hooks["pre_llm_call"](session_id="s1", user_message="fix the login 500") is None
    hooks["post_tool_call"](tool_name="patch", args={"path": "auth.py"}, result="{}", session_id="s1")
    hooks["post_llm_call"](session_id="s1", assistant_response="Fixed the login 500; it was a null session.")
    assert "auth.py" in coach.loop_state("s1")["last_log"][0]
    out = hooks["pre_llm_call"](session_id="s1", user_message="still getting the 500")
    assert out and "login 500" in out["context"] and "reproduce" in out["context"]
    hooks["post_llm_call"](session_id="s1", assistant_response="Fixed for real this time.")
    out2 = hooks["pre_llm_call"](session_id="s1", user_message="nope, still 500")
    assert "Stop iterating" in out2["context"]
    assert store.session("s1").get("confirmed_fail") == 2
