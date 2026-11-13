"""status_fresh (self-heal agent-02be078e8a): a person's progress ask answered from memory.

The ask and the draft are from a reported chat (scrubbed): the user asked "what going on here, why
are we blocked and nothing running"; the first reply, written with zero tool calls, said the follow-ups "were assigned
to a cloud worker but never started" while PR #2549 was written, green and in review. The previous reply had said "a
cloud worker is tidying ... now". status_plain fired but only asked for a plain opening; relay_repeat matched neither
the ask nor the relay; the coach's judge flagged the draft only after it had been on screen ~37 s.
"""
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def _mod(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rc = _mod("t_status_fresh_rc", "reply_check.py")
rr = _mod("t_status_fresh_rr", "relay_repeat.py")

ASK = "what going on here, why are we blocked and nothing running"
# the reply before the ask (its owner relay)
PREV = ("Done with the session-start notes. A cloud worker is tidying the two follow-ups from the session start now; "
        "I'll report when they land.")
# draft d_836247be50ad46e2 (scrubbed)
DRAFT = ("Nothing is running because the two session-start follow-ups were assigned to a cloud worker but never "
         "started. The cards are blocked behind a full worker pool, so no PR exists yet.")
CARD_READ = [
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "kanban_show", "arguments": "{\"task_id\": \"t_01a1d6\"}"}}]},
    {"role": "tool", "tool_call_id": "c1", "content": "{\"status\": \"running\"}"},
]


def _hooks(tmp_path, monkeypatch):
    mod = _mod("coach_init_sf", "__init__.py")
    core = mod._load("core")
    flm = mod._load("fix_loop")
    monkeypatch.setattr(flm, "aux_judge", lambda s, p: json.dumps({"p_failed_fix": 0.1, "issue": ""}))
    monkeypatch.setattr(mod._load("relay_repeat"), "board_card_status", lambda c: "running")
    store = core.Store(root=tmp_path / "coach")
    _, hooks = mod.build(store=store, checkout=None)
    return hooks, tmp_path / "coach" / "log.jsonl"


def _turn(user, *rows):
    return [{"role": "user", "content": user}, *rows]


def _events(log, name):
    if not log.exists():
        return []
    return [e for e in (json.loads(l) for l in log.read_text().splitlines()) if e.get("event") == name]


def test_status_plain_hint_asks_for_a_live_read_first():
    h = rc.status_plain_hint(ASK)
    assert h and "plain words" in h and "first paragraph" in h
    assert "live state" in h and "this turn" in h and "never started" in h


def test_zero_tool_status_draft_is_held_unshown(tmp_path, monkeypatch):
    hooks, log = _hooks(tmp_path, monkeypatch)
    gate = hooks["pre_final_gate:status_fresh"]
    hooks["pre_llm_call"](session_id="s1", user_message="tidy up the follow-ups")
    hooks["post_llm_call"](session_id="s1", assistant_response=PREV)
    out = hooks["pre_llm_call"](session_id="s1", user_message=ASK)
    assert out and "live state" in out["context"]
    assert gate.needs_buffer is True  # the held draft must not stream
    res = gate(session_id="s1", platform="desktop", final_response=DRAFT,
               turn_messages=_turn(ASK, {"role": "assistant", "content": DRAFT}), attempt=0)
    assert res and res.get("hold") is True and "status_fresh" in res["nudge"]
    ev = _events(log, "status_fresh")
    assert ev and ev[-1]["action"] == "hold" and ev[-1]["session_id"] == "s1" and ev[-1]["tools"] == 0
    # once per turn: the redraft is never held again by this gate
    assert gate(session_id="s1", platform="desktop", final_response=DRAFT,
                turn_messages=_turn(ASK, {"role": "assistant", "content": DRAFT}), attempt=1) is None


def test_buffer_eligibility_is_session_scoped_and_cleared(tmp_path, monkeypatch):
    hooks, _ = _hooks(tmp_path, monkeypatch)
    gate = hooks["pre_final_gate:status_fresh"]
    hooks["pre_llm_call"](session_id="status", user_message=ASK)
    hooks["pre_llm_call"](session_id="ordinary", user_message="add a dark mode toggle")
    assert gate.needs_buffer_for_session(session_id="status") is True
    assert gate.needs_buffer_for_session(session_id="ordinary") is False
    assert gate.needs_buffer_for_session(session_id="unknown") is False
    hooks["post_llm_call"](session_id="status", assistant_response="Checked the board.")
    assert gate.needs_buffer_for_session(session_id="status") is False
    assert gate.needs_buffer is False


def test_same_draft_after_a_card_read_passes(tmp_path, monkeypatch):
    hooks, log = _hooks(tmp_path, monkeypatch)
    gate = hooks["pre_final_gate:status_fresh"]
    hooks["pre_llm_call"](session_id="s2", user_message=ASK)
    res = gate(session_id="s2", platform="desktop", final_response=DRAFT,
               turn_messages=_turn(ASK, *CARD_READ, {"role": "assistant", "content": DRAFT}), attempt=0)
    assert res is None
    ev = _events(log, "status_fresh")
    assert ev and ev[-1]["action"] == "pass" and ev[-1]["tools"] == 1


def test_non_status_ask_does_not_buffer_or_hold(tmp_path, monkeypatch):
    hooks, log = _hooks(tmp_path, monkeypatch)
    gate = hooks["pre_final_gate:status_fresh"]
    hooks["pre_llm_call"](session_id="s3", user_message="add a dark mode toggle to the settings page")
    assert gate.needs_buffer is False
    assert gate(session_id="s3", platform="desktop", final_response=DRAFT,
                turn_messages=_turn("add a dark mode toggle", {"role": "assistant", "content": DRAFT}),
                attempt=0) is None
    assert _events(log, "status_fresh") == []


def test_status_turn_without_status_claims_passes(tmp_path, monkeypatch):
    hooks, _ = _hooks(tmp_path, monkeypatch)
    gate = hooks["pre_final_gate:status_fresh"]
    hooks["pre_llm_call"](session_id="s4", user_message=ASK)
    assert gate(session_id="s4", platform="desktop", final_response="Let me check the board first.",
                turn_messages=_turn(ASK), attempt=0) is None


def test_off_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_STATUS_FRESH", "off")
    hooks, log = _hooks(tmp_path, monkeypatch)
    gate = hooks["pre_final_gate:status_fresh"]
    hooks["pre_llm_call"](session_id="s5", user_message=ASK)
    assert gate.needs_buffer is False
    assert gate(session_id="s5", platform="desktop", final_response=DRAFT,
                turn_messages=_turn(ASK, {"role": "assistant", "content": DRAFT}), attempt=0) is None


def test_draft_status_claim_detection():
    for t in (DRAFT, "PR #2549 is merged.", "t_01a1d6e9579b92eb is still running", "The card is blocked on review.",
              "It hasn't started yet."):
        assert rc.status_fresh_claim(t), t
    for t in ("Let me check the board first.", "Sure, I'll add the toggle.", "Thanks!"):
        assert rc.status_fresh_claim(t) is None, t


def test_relay_repeat_matches_the_new_phrasings():
    for t in (ASK, "why are we blocked", "why is nothing running", "what going on?"):
        assert rr.repeat_ask(t), t
    assert rr.relay_owner(PREV), PREV
    hit = rr.assess({"last_reply": PREV}, 7, ASK, card_status=lambda c: "running")
    assert hit and "yourself this turn" in hit["hint"]
