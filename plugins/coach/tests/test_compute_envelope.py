"""Compute envelope: an agent asking a human to approve small GPU compute/spend gets a note to rent within the default
budget and carry on, at the two places it happened (kanban_block, the final reply). Bigger asks still reach the user.

The fixtures are verbatim from defect agent-962f3f10ff: card t_bf1d2f3d's 10-27 07:30 UTC block and the origin
session's 10-27 16:41 PT reply, which left a $5-10 experiment blocked for 5 days.
"""
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]

REAL_BLOCK_REASON = (
    "Slurm is unavailable on metta1 (no config/slurmd; sinfo/squeue fail since at least 10-24), so the only existing "
    "compute is the idle RTX 4090 on metta1 itself. Decision: may I run the pilot and 12 confirmation arms directly on "
    "that GPU as user metta (outside Slurm), bounded: one process at a time, nice 19, work dir /tmp/metta-softmaxfix, "
    "~5-15 GPU-h, staying out of the 70G free disk beyond ~10G? Reply 'go direct on metta1' to approve, or restore "
    "Slurm / name another existing allocation / approve a rental with a budget. Then unblock this card. No acceptance "
    "is being relaxed.")
REAL_HUMAN_REQUEST = {
    "action": "Approve running bounded training directly on metta1's idle RTX 4090 outside Slurm, or restore Slurm / "
              "name other compute",
    "why_human": "Running outside the scheduler on a shared host needs owner approval; previous sessions declined to "
                 "bypass it",
    "interaction": "reply", "scope": "metta1 RTX 4090, uid metta, /tmp/metta-softmaxfix, ~5-15 GPU-h",
    "evidence": "sinfo fails: no slurm config; GPU idle at 07:30Z; disk 70G free; 0/12 arms run"}
REAL_MENU_REPLY = (
    "The blocker has changed: **the task is waiting for a compute choice, not the old credential approval.** Slurm was "
    "unavailable at the worker\u2019s last check; no GPU training has run.\n\n**I recommend a dedicated rented GPU with "
    "a $20 total cap**, including setup time and automatic shutdown. That avoids bypassing scheduling on shared "
    "`metta1`.\n\n::user-step{id=\"us_110a409768\" s=\"20261023_231233_1ccf1c\" t=\"Reply **\u201capprove $20\u201d** "
    "and I\u2019ll authorize that route and resume the existing task. Alternatively, **\u201cuse metta1 directly\u201d** "
    "authorizes a bounded run there after fresh GPU-occupancy and disk checks.\"}")
BIG_ASK = ("The full sweep needs an 8x H100 node for about 30 hours, roughly $200 total. Do you approve the $200 "
           "rental, or should I cut it to the 3 most important arms?")
SSH_ASK = "The SSH grant for metta1 expired. Please approve token-broker request 7b93 so I can check the GPU host."
DONE_REPORT = ("Rented one RTX 4090 on Vast at $0.45/h (cheapest by $/finished run), ran the pilot and 12 arms in 11 h, "
               "$5.10 total. Results are copied off and the instance is destroyed (vastai show instances: empty).")


def _hooks(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("coach_init_ce", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setenv("COACH_REPLY_RULES", "none")  # isolate from the LLM reply rules: no judge call
    core = mod._load("core")
    store = core.Store(root=tmp_path / "coach")
    _, hooks = mod.build(store=store, checkout=None)
    return hooks


def _log(tmp_path):
    p = tmp_path / "coach" / "log.jsonl"
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


def test_real_block_gets_the_envelope_note_then_a_repeat_is_an_agent_fault(tmp_path, monkeypatch):
    hooks = _hooks(tmp_path, monkeypatch)
    args = {"reason": REAL_BLOCK_REASON, "kind": "needs_input", "human_request": REAL_HUMAN_REQUEST}
    out = hooks["pre_tool_call"](tool_name="kanban_block", args=args, session_id="w1")
    assert out and out["action"] == "block", out
    assert "$25" in out["message"] and "rent" in out["message"].lower()
    assert any(r["event"] == "agent_hint" and r.get("rule") == "compute_envelope" for r in _log(tmp_path))
    # Told once; blocking anyway goes through (the agent may know something we don't) but is logged as a fault.
    assert hooks["pre_tool_call"](tool_name="kanban_block", args=args, session_id="w1") is None
    assert any(r["event"] == "agent_fault" and r.get("rule") == "compute_envelope" for r in _log(tmp_path))


def test_block_through_deferred_tool_call_is_checked_too(tmp_path, monkeypatch):
    hooks = _hooks(tmp_path, monkeypatch)
    args = {"calls": [{"name": "kanban_block", "arguments": {"reason": REAL_BLOCK_REASON}}]}
    out = hooks["pre_tool_call"](tool_name="tool_call", args=args, session_id="w2")
    assert out and out["action"] == "block"


def test_real_approve_20_menu_reply_gets_the_note(tmp_path, monkeypatch):
    hooks = _hooks(tmp_path, monkeypatch)
    hooks["pre_llm_call"](session_id="s1", user_message="help me unblock your task")
    out = hooks["pre_verify"](session_id="s1", attempt=0, final_response=REAL_MENU_REPLY)
    assert out and out["action"] == "continue", out
    assert "$25" in out["message"]
    assert any(r["event"] == "reply_nudge" and r.get("rule") == "compute_envelope" for r in _log(tmp_path))
    # Still asking after the note -> recorded as an agent fault (the turn is not held twice).
    assert hooks["pre_verify"](session_id="s1", attempt=1, final_response=REAL_MENU_REPLY) is None
    assert any(r["event"] == "agent_fault" and r.get("rule") == "compute_envelope" for r in _log(tmp_path))


def test_big_multi_gpu_ask_still_reaches_the_user(tmp_path, monkeypatch):
    hooks = _hooks(tmp_path, monkeypatch)
    assert hooks["pre_tool_call"](tool_name="kanban_block", args={"reason": BIG_ASK}, session_id="w3") is None
    hooks["pre_llm_call"](session_id="s3", user_message="run the sweep")
    assert hooks["pre_verify"](session_id="s3", attempt=0, final_response=BIG_ASK) is None


def test_credential_asks_and_finished_reports_are_left_alone(tmp_path, monkeypatch):
    hooks = _hooks(tmp_path, monkeypatch)
    assert hooks["pre_tool_call"](tool_name="kanban_block", args={"reason": SSH_ASK}, session_id="w4") is None
    hooks["pre_llm_call"](session_id="s4", user_message="status?")
    assert hooks["pre_verify"](session_id="s4", attempt=0, final_response=DONE_REPORT) is None
    assert hooks["pre_tool_call"](tool_name="kanban_comment", args={"body": REAL_BLOCK_REASON},
                                  session_id="w4") is None


def test_classifier_cases():
    spec = importlib.util.spec_from_file_location("coach_ce_mod", HERE / "compute_envelope.py")
    ce = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ce)
    assert ce.classify("Should I rent a 4090 for ~$8 to run this?")
    assert ce.classify("Want me to spin up a single A100 on RunPod (about $15)?")
    assert ce.classify("Approve $15 of GPU time and I'll start.")
    assert not ce.classify("Approve $40 of GPU time and I'll start.")  # over the envelope
    assert not ce.classify("May I rent a 4x A100 node for 2 hours ($18)?")  # multi-GPU
    assert not ce.classify("Want me to merge the PR?")
    assert not ce.classify("If the run needs more than $25 I will ask you first.")
