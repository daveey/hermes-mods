"""Complaint judge sees the user's earlier restraints (agent-d086774bf9): a wait the user imposed
("document it, don't create it") must not read as an agent-owned block."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sh = load("coach_self_heal_test_scope", ROOT / "self_heal.py")
core = load("coach_core_test_scope", ROOT / "core.py")
plugin = load("coach_plugin_test_scope", ROOT / "__init__.py")

ORIGINAL = ("Set up Access bb:ring for the ring feed (document it, don't create it). Then continue with the "
            "docs. " + "filler " * 400)
FINAL = "Blocked: the user must create the Access application."


def judge(p=0.9):
    calls = []

    def llm(system, prompt):
        calls.append((system, prompt))
        return json.dumps({"p_defect": p, "kind": "admission", "component": "agent",
                           "symptom": "blocked on user creating access application"})
    return llm, calls


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("COACH_SELFHEAL", "shadow")


def run(tmp_path, hist, msg):
    llm, calls = judge()
    _, hooks = plugin.build(store=core.Store(tmp_path / "coach"), checkout=None, selfheal_sync=True, selfheal_llm=llm)
    hooks["pre_llm_call"](session_id="s1", user_message=msg, conversation_history=hist)
    return calls


def u(t):
    return {"role": "user", "content": t}


def a(t):
    return {"role": "assistant", "content": t}


def test_hook_prompt_carries_original_prohibition(tmp_path, on):
    calls = run(tmp_path, [u(ORIGINAL), a(FINAL)], "why are you blocked")
    prompt = calls[0][1]
    assert "document it, don't create it" in prompt
    assert prompt.index("EARLIER USER INSTRUCTIONS") < prompt.index("AGENT'S PREVIOUS FINAL MESSAGE")
    assert prompt.count("filler") < 40 and len(prompt) < 1500  # snippet, not the whole 2.4k message
    assert "why are you blocked" in prompt


def test_no_prohibition_wait_stays_detectable(tmp_path, on):
    hist = [u("Set up Access bb:ring for the ring feed."), a(FINAL)]
    calls = run(tmp_path, hist, "why are you blocked")
    assert "EARLIER USER INSTRUCTIONS" not in calls[0][1]
    assert sh.detect_complaint(FINAL, "why are you blocked", llm=judge()[0], threshold=0.6)  # still a hit


def test_later_instruction_change_in_chronological_order(tmp_path, on):
    hist = [u(ORIGINAL), a("ok"), u("actually go ahead and create the app"), a(FINAL)]
    prompt = run(tmp_path, hist, "why are you blocked")[0][1]
    first, later = prompt.index("don't create it"), prompt.index("actually go ahead and create the app")
    assert first < later
    assert "[user message 1/2, restraint]" in prompt and "[user message 2/2]" in prompt


def test_approval_before_restraint_is_not_shown_as_after(tmp_path, on):
    hist = [u("go ahead and create whatever you need"), a("ok"), u("but don't create the access app"), a(FINAL)]
    prompt = run(tmp_path, hist, "why are you blocked")[0][1]
    assert "go ahead and create whatever" not in prompt and "don't create the access app" in prompt


def test_history_ending_with_current_message_is_not_duplicated():
    s = sh.request_scope([u("don't deploy it"), a("ok"), u("why are you blocked")], "why are you blocked")
    assert s.count("why are you blocked") == 0 and "don't deploy it" in s


def test_polite_dont_worry_is_not_a_restraint():
    assert sh.request_scope([u("don't worry about the rush, fix the sidebar"), a("x")], "still broken") == ""


def test_digest_prompt_unchanged_without_scope():
    base = sh.complaint_prompt("prev", "msg", None, "Session title: T")
    assert base == sh.complaint_prompt("prev", "msg", None, "Session title: T", "")
    assert "SESSION CONTEXT" in base and "EARLIER USER INSTRUCTIONS" not in base
