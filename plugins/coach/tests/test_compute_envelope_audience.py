"""Compute envelope audience: the under-use check (`classify`, "you are asking a human to approve in-envelope spend")
reads only text addressed to a human. A kanban_create that runs at once (no initial_status="blocked", no
human_request) is a brief for a worker, so its body is never an ask to David. compute_authority still reads every
card body: a brief can declare a cap.

Live false hold (MacBook, 2026-11-02 ~18:00 PT, unconventional-collab orchestrator): a running plan-only card whose
body listed PLAN.md section names was refused with the "rent one GPU up to $25" note. The held sentence, verbatim:
"- tests and acceptance gates; compute quote and approval stages A/B/C."
"""
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]

HELD_LINE = "- tests and acceptance gates; compute quote and approval stages A/B/C."
WORKER_BODY = (
    "Write PLAN.md for the unconventional-collab pilot. Plan only: this card needs no GPU and rents nothing.\n\n"
    "PLAN.md sections:\n"
    "- goal, prior work and the hypothesis;\n"
    "- arms and metrics;\n"
    f"{HELD_LINE}\n\n"
    "Acceptance: PLAN.md committed; every section filled.")
# A real in-envelope ask, as a blocked card would carry it (defect agent-962f3f10ff's wording).
REAL_ASK = ("Slurm is down on metta1. Decision: may I rent one RTX 4090 on Vast for about $10 total to run the pilot? "
            "Reply 'approve' and unblock this card.")
HUMAN_REQUEST = {"action": "Approve renting one 4090 on Vast for $10. Unblock = rent; archive = drop.",
                 "why_human": "spend", "interaction": "approval", "scope": "one GPU, $10", "evidence": "Slurm down"}


def _mod():
    spec = importlib.util.spec_from_file_location("coach_init_aud", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _hooks(tmp_path, monkeypatch):
    mod = _mod()
    monkeypatch.setenv("COACH_REPLY_RULES", "none")
    store = mod._load("core").Store(root=tmp_path / "coach")
    _, hooks = mod.build(store=store, checkout=None)
    return hooks


def _log(tmp_path):
    p = tmp_path / "coach" / "log.jsonl"
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


def _create(hooks, args, deferred=False, sid="aud"):
    if deferred:
        return hooks["pre_tool_call"](tool_name="tool_call", session_id=sid,
                                      args={"calls": [{"name": "kanban_create", "arguments": args}]})
    return hooks["pre_tool_call"](tool_name="kanban_create", args=args, session_id=sid)


def test_the_held_line_still_reads_as_an_ask_to_the_classifier():
    """The sentence matches ASK_RE + COMPUTE_RE; the fix is the audience, so this stays a classifier hit."""
    assert _mod()._load("compute_envelope").classify(WORKER_BODY)


@pytest.mark.parametrize("deferred", [False, True])
@pytest.mark.parametrize("extra", [{}, {"initial_status": "running"}, {"assignee": "default", "parents": ["t_x"]}])
def test_running_worker_card_body_is_not_an_envelope_ask(tmp_path, monkeypatch, deferred, extra):
    hooks = _hooks(tmp_path, monkeypatch)
    for body in (WORKER_BODY, HELD_LINE, REAL_ASK):
        assert _create(hooks, {"title": "plan", "body": body, **extra}, deferred) is None
    assert not any(r.get("rule") == "compute_envelope" for r in _log(tmp_path))
    assert not any(r.get("event") == "agent_fault" for r in _log(tmp_path))


@pytest.mark.parametrize("deferred", [False, True])
@pytest.mark.parametrize("args", [
    {"title": "rent?", "body": REAL_ASK, "initial_status": "blocked", "human_request": HUMAN_REQUEST},
    {"title": "rent?", "body": REAL_ASK, "initial_status": "blocked"},
    {"title": "rent?", "body": "Pilot card.", "initial_status": "blocked", "human_request": HUMAN_REQUEST},
    {"title": "rent?", "body": "Pilot card.", "human_request": HUMAN_REQUEST},
])
def test_card_addressed_to_a_human_is_still_held(tmp_path, monkeypatch, deferred, args):
    hooks = _hooks(tmp_path, monkeypatch)
    out = _create(hooks, args, deferred)
    assert out and out["action"] == "block" and "$25" in out["message"]
    assert any(r.get("event") == "agent_hint" and r.get("rule") == "compute_envelope" for r in _log(tmp_path))


def test_block_and_reply_asks_are_still_held(tmp_path, monkeypatch):
    hooks = _hooks(tmp_path, monkeypatch)
    out = hooks["pre_tool_call"](tool_name="kanban_block", args={"reason": REAL_ASK}, session_id="b")
    assert out and out["action"] == "block"
    out = hooks["pre_tool_call"](tool_name="kanban_block", session_id="b2",
                                 args={"reason": "Pilot card.", "human_request": HUMAN_REQUEST})
    assert out and out["action"] == "block"
    out = hooks["pre_final_gate:compute_envelope"](session_id="r", final_response=REAL_ASK)
    assert out and out["hold"]


# Replay of the hub's coach log (2026-11-03 00:29 UTC): a blocked create asking David to approve an over-$25,
# multi-GPU upgrade. Its human_request action says what unblock does; read as authority text, that line is a
# multi-GPU declaration. The ask goes to David as it is (over the envelope), so neither check may hold it.
UPGRADE_ASK = {
    "title": "Agent-World upgrade decision", "initial_status": "blocked",
    "body": "Upgrade decision for the Agent-World reproduction. See REPRO_PLAN.md.",
    "human_request": {
        "action": "Reply in this chat with B-full, C, or no. Unblock = run the full 44K SFT on 8\u00d7H100 "
                  "(~$150\u2013250) after the \u2264$25 tier finishes; add C to also build GRPO on the 563 envs "
                  "(multi-GPU, days, $500+). Archive = stop at the \u2264$25 tier (the default if you stay silent).",
        "why_human": "Spend above the $25 single-GPU standing budget, plus multi-GPU, needs David's approval.",
        "interaction": "approval", "scope": "Agent-World reproduction compute only.",
        "evidence": "A full-FT epoch of an 8B model is ~8 h on 8\u00d7H100."}}


@pytest.mark.parametrize("deferred", [False, True])
def test_blocked_over_envelope_ask_reaches_the_human(tmp_path, monkeypatch, deferred):
    hooks = _hooks(tmp_path, monkeypatch)
    assert _create(hooks, UPGRADE_ASK, deferred) is None
    assert not any(str(r.get("rule", "")).startswith("compute") for r in _log(tmp_path))


@pytest.mark.parametrize("deferred", [False, True])
def test_running_worker_card_cap_declaration_is_still_compute_authority(tmp_path, monkeypatch, deferred):
    hooks = _hooks(tmp_path, monkeypatch)
    body = WORKER_BODY + "\nRent 8 H100s on Vast with a $3000 cap for stage B."
    out = _create(hooks, {"title": "plan", "body": body}, deferred)
    assert out and out["action"] == "block"
    assert any(r.get("rule") == "compute_authority" for r in _log(tmp_path))
