"""compute_authority on a kanban_block human_request.

A human_request is the ask itself. Its action must say what unblock and archive mean (kanban_block refuses an action
without them), so a genuine over-envelope ask names the over-envelope run in exactly those clauses: "Unblock = run the
full SFT on 8xH100 (~$150-250)". Read as authority text, that clause is the agent declaring multi-GPU, and the coach
held the very ask AGENTS.md wants filed (hub coach log 2026-11-03 00:29 UTC, session 20261103_002846_644342; the
create path was fixed in 17db22be, the block path still held it).

The rule: when the human_request's consequence clauses (unblock/archive/yes/no = ..., and `choices`) name an
over-envelope figure or multi-GPU, the over-envelope compute is what David is being asked to decide, so the
human_request is not authority text. The reason field is still read, and a human_request whose answer does not
decide the compute ("Unblock = continue" beside a "$3000 cap" scope) is still read, so a declared cap is still held.
"""
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]

UPGRADE_HR = {
    "action": "Reply in this chat with B-full, C, or no. Unblock = run the full 44K SFT on 8\u00d7H100 "
              "(~$150\u2013250) after the \u2264$25 tier finishes; add C to also build GRPO on the 563 envs "
              "(multi-GPU, days, $500+). Archive = stop at the \u2264$25 tier (the default if you stay silent).",
    "why_human": "Spend above the $25 single-GPU standing budget, plus multi-GPU, needs David's approval.",
    "interaction": "approval", "scope": "Agent-World reproduction compute only.",
    "evidence": "A full-FT epoch of an 8B model is ~8 h on 8\u00d7H100."}
REPRO = {"reason": "Upgrade decision for the Agent-World reproduction.", "kind": "needs_input",
         "human_request": UPGRADE_HR}


def _mod():
    spec = importlib.util.spec_from_file_location("coach_init_bhr", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _hooks(tmp_path, monkeypatch):
    mod = _mod()
    monkeypatch.setenv("COACH_REPLY_RULES", "none")
    store = mod._load("core").Store(root=tmp_path / "coach")
    _, hooks = mod.build(store=store, checkout=None)
    return mod, hooks


def _log(tmp_path):
    p = tmp_path / "coach" / "log.jsonl"
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


def _block(hooks, args, deferred=False, sid="bhr"):
    if deferred:
        return hooks["pre_tool_call"](tool_name="tool_call", session_id=sid,
                                      args={"calls": [{"name": "kanban_block", "arguments": args}]})
    return hooks["pre_tool_call"](tool_name="kanban_block", args=args, session_id=sid)


def test_repro_authority_on_block_text_is_none(tmp_path, monkeypatch):
    ce = _hooks(tmp_path, monkeypatch)[0]._load("compute_envelope")
    assert ce.authority(ce.block_text("kanban_block", REPRO)) is None
    assert ce.authority(ce.block_text("tool_call", {"calls": [{"name": "kanban_block", "arguments": REPRO}]})) is None


@pytest.mark.parametrize("deferred", [False, True])
def test_over_envelope_block_ask_reaches_the_human(tmp_path, monkeypatch, deferred):
    hooks = _hooks(tmp_path, monkeypatch)[1]
    assert _block(hooks, REPRO, deferred) is None
    assert _block(hooks, REPRO, deferred) is None  # and no agent_fault on a retry
    assert not any(str(r.get("rule", "")).startswith("compute") for r in _log(tmp_path))


@pytest.mark.parametrize("deferred", [False, True])
@pytest.mark.parametrize("hr", [
    {"action": "Reply yes or no. Yes = rent 4 H100s on Lambda for the sweep, about $400 total; no = stay on one GPU.",
     "why_human": "multi-GPU spend", "interaction": "approval", "scope": "sweep compute", "evidence": "plan"},
    {"action": "Pick one. Unblock = run the option you pick; archive = stop at the $25 tier.",
     "why_human": "over-envelope spend", "interaction": "approval", "scope": "sweep",
     "evidence": "8xH100 needed for the full run",
     "choices": [{"label": "Full run on 8xH100 (~$200)", "value": "full"}, {"label": "Stop", "value": "stop"}]},
])
def test_other_over_envelope_ask_shapes_reach_the_human(tmp_path, monkeypatch, deferred, hr):
    hooks = _hooks(tmp_path, monkeypatch)[1]
    assert _block(hooks, {"reason": "Compute decision.", "human_request": hr}, deferred) is None
    assert not any(str(r.get("rule", "")).startswith("compute") for r in _log(tmp_path))


@pytest.mark.parametrize("deferred", [False, True])
@pytest.mark.parametrize("args", [
    # The agent declares the cap in the reason: the ask beside it does not excuse it.
    {"reason": "I set the GPU rental cap at $3000 on Vast for stage B.", "human_request": UPGRADE_HR},
    # The answer decides nothing about compute: the scope's cap is the agent's premise, not the ask.
    {"reason": "Starting stage B.", "human_request": {
        "action": "FYI only. Unblock = continue; archive = stop.", "why_human": "status",
        "interaction": "reply", "scope": "GPU rental cap $3000 on Vast", "evidence": "plan"}},
    # A declaration after the consequence clause ends (";") is not part of it.
    {"reason": "Stage B.", "human_request": {
        "action": "Unblock = continue; I set the cap at $3000 on Vast. Archive = stop.", "why_human": "status",
        "interaction": "reply", "scope": "stage B", "evidence": "plan"}},
])
def test_declared_cap_beside_a_block_ask_is_still_held(tmp_path, monkeypatch, deferred, args):
    hooks = _hooks(tmp_path, monkeypatch)[1]
    out = _block(hooks, args, deferred)
    assert out and out["action"] == "block"
    assert any(r.get("rule") == "compute_authority" for r in _log(tmp_path))


# The kanban_create that filed this fix (hub coach log 2026-11-03 07:23 UTC, session local-8d0499d7...): a running
# card's brief citing a test fixture was held. `unquoted` paired the closing tick of `choices` with the next opening
# tick and blanked the prose between, and the sentence naming the fixture's "$3000 cap" read as a declaration.
CARD_BODY = ("That human_request is exactly the over-$25 / multi-GPU ask AGENTS.md wants filed as a blocked card, "
             "yet the coach holds it once (and logs agent_fault on retry).\n\n"
             "## Fix (design yours; guard false-positive fixes are standing-approved)\n"
             "Candidate: the consequence clauses of a human_request (`Unblock = \u2026`, `Archive = \u2026`, `choices`) "
             "describe what David's answer does, so they are not an agent declaration. Keep "
             "`tests/test_compute_mentions.py::test_cap_inside_human_request_is_still_held` (an FYI human_request "
             "whose scope sets a $3000 cap) held, or show why it should change.")


def test_unquoted_pairs_backticks_left_to_right(tmp_path, monkeypatch):
    ce = _hooks(tmp_path, monkeypatch)[0]._load("compute_envelope")
    out = ce.unquoted("a (`x = 1`, `choices`) b c. Keep `tests/t.py::t` d.")
    assert "b c. Keep" in out and "`choices`" in out and "x = 1" not in out


@pytest.mark.parametrize("deferred", [False, True])
def test_worker_brief_citing_a_test_fixture_cap_is_not_authority(tmp_path, monkeypatch, deferred):
    hooks = _hooks(tmp_path, monkeypatch)[1]
    args = {"title": "coach fix", "body": CARD_BODY, "assignee": "default"}
    call = ({"tool_name": "tool_call", "args": {"calls": [{"name": "kanban_create", "arguments": args}]}}
            if deferred else {"tool_name": "kanban_create", "args": args})
    assert hooks["pre_tool_call"](session_id="brief", **call) is None
    assert not any(str(r.get("rule", "")).startswith("compute") for r in _log(tmp_path))


def test_a_cap_declared_next_to_a_test_name_with_a_commitment_is_still_held(tmp_path, monkeypatch):
    hooks = _hooks(tmp_path, monkeypatch)[1]
    body = "Per test_stage_b I will rent 8 H100s on Vast with a $3000 cap."
    out = hooks["pre_tool_call"](tool_name="kanban_create", session_id="c", args={"title": "b", "body": body})
    assert out and out["action"] == "block"
