"""The backstop reads WHY a model wait was interrupted (self-heal agent-38bad5aced).

Core stamps ``display_metadata.interrupt = {reason, waited_s, prompt_tokens}`` on the interrupted row and the
session digest carries it on the next user turn as ``prev_interrupt``. Before this every "Operation interrupted:
waiting for model response" followed by "resume" was judged a bare nudge after a broken turn and filed as an
agent fault, whether the user pressed Stop on a slow first token or a backend exit lost the turn.
"""
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sh = _load("coach_self_heal_test_ir", ROOT / "self_heal.py")
core = _load("coach_core_test_ir", ROOT / "core.py")


def _digest(tmp_path, now, interrupt, text="resume"):
    p = tmp_path / "data" / "mac" / "default" / "s1.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    turn = {"t": now - 60, "text": text,
            "prev": "Operation interrupted: waiting for model response (106.6s elapsed)."}
    if interrupt:
        turn["prev_interrupt"] = interrupt
    p.write_text(json.dumps({"id": "s1", "host": "mac", "profile": "default", "source": "desktop", "title": "x",
                             "user_turns": [turn]}))
    return tmp_path / "data"


def _judge(p=0.9):
    calls = []

    def llm(system, prompt):
        calls.append(prompt)
        return json.dumps({"p_defect": p, "kind": "agent_fault", "component": "agent",
                           "symptom": "model response stalled no answer before interruption"})
    return llm, calls


def _events(store, name):
    return [e for e in store.read_log() if e.get("event") == name]


def _run(tmp_path, interrupt, text="resume"):
    now = time.time()
    data = _digest(tmp_path, now, interrupt, text)
    store = core.Store(tmp_path / "coach")
    llm, calls = _judge()
    filed = []
    sh.backstop(data, store=store, judge=llm, now=now, m="shadow", confirm=None,
                filer=lambda d, store=None, m=None: filed.append(d) or d)
    return store, calls, filed


def test_user_stop_after_a_long_wait_is_a_slow_model_event_not_an_agent_fault(tmp_path):
    store, calls, filed = _run(tmp_path, {"reason": "user_stop", "waited_s": 106.6, "prompt_tokens": 320000})
    assert filed == [] and calls == []                       # never reaches the judge, never files
    (ev,) = _events(store, "selfheal_slow_model")
    assert ev["waited_s"] == 106.6 and ev["prompt_tokens"] == 320000 and ev["session_id"] == "s1"


def test_user_stop_followed_by_a_real_complaint_is_still_judged(tmp_path):
    _store, calls, _filed = _run(tmp_path, {"reason": "user_stop", "waited_s": 106.6, "prompt_tokens": 1000},
                                 text="you keep hanging on every call, root cause it")
    assert calls, "a complaint after a Stop must still be judged"


def test_backend_exit_with_no_auto_continue_files_a_lost_turn(tmp_path):
    _store, calls, filed = _run(tmp_path, {"reason": "backend_exit", "waited_s": 3.0, "prompt_tokens": 5000})
    assert calls == []                                       # machine signal, no judge
    (d,) = filed
    assert d["kind"] == "hermes_bug" and d["component"] == "gateway"
    assert "backend exit" in d["symptom"] and d["source"] == "backstop"


def test_row_without_a_reason_is_judged_as_before(tmp_path):
    _store, calls, filed = _run(tmp_path, None)
    assert calls and len(filed) == 1


@pytest.mark.parametrize("reason", ["ws_orphan", "lease_takeover"])
def test_other_system_reasons_fall_through_to_the_judge(tmp_path, reason):
    _store, calls, _filed = _run(tmp_path, {"reason": reason, "waited_s": 5.0, "prompt_tokens": 5000})
    assert calls
