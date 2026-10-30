"""Block-time handoff check (self-heal agent-19492bff76).

A hub Kanban worker asked David to open a tailnet URL and press Create/Install after ONE refused private-URL
navigation ("the browser tool refuses private tailnet URLs so prefill by agent is impossible"). The reply-time handoff
check never saw it: kanban_block ended the run first. pre_tool_call now judges a human-facing block once per
session and card; the same block again goes through, so it can never loop.
"""
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent

INCIDENT_REASON = (
    "Two clicks to create and install the dedicated runners-broker GitHub App (decision B, already approved). You "
    "must be on Tailscale.\n1) Open http://10.0.0.7:18765/ in a browser signed in to GitHub as you, press "
    "\"Continue to GitHub\", then \"Create GitHub App\".\n2) Click Install, choose you/repo only -> Install.\n"
    "3) Unblock this card. Signed-in browser prefill not possible (browser tool refuses private/tailnet URLs).")
INCIDENT_HR = {"action": "Open http://10.0.0.7:18765/ (Tailscale) signed in as you; click Create, then "
                         "Install. Unblock = I deploy; archive = drop it.",
               "why_human": "the browser tool refuses private tailnet URLs so prefill by agent is impossible",
               "interaction": "external", "scope": "one GitHub App", "evidence": "browser_exec refused the URL"}
PASSKEY_REASON = "GitHub asks for the user's passkey (sudo mode) on the Install page; only he holds it."
REFUSAL = '{"error": "Blocked: URL targets a private or internal address"}'


def _hooks(tmp_path, monkeypatch, p_handoff=0.95):
    spec = importlib.util.spec_from_file_location("coach_init_bh", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for k in ("COACH_REPLY_RULES", "COACH_BLOCK_TIME"):
        monkeypatch.delenv(k, raising=False)
    calls = []

    def judge(system, prompt):
        calls.append(prompt)
        p = 0.1 if "passkey" in prompt else p_handoff
        return json.dumps({"handoff": p, "confirm": 0, "unverified": 0, "unwatched": 0, "report_only": 0,
                           "evidence": "asks David to open the URL and click Create"})

    rc = mod._load("reply_check")
    monkeypatch.setattr(rc, "aux_judge", judge)
    monkeypatch.setattr(mod._load("second_opinion"), "_config_settings", lambda: {})
    store = mod._load("core").Store(root=tmp_path / "coach")
    _, hooks = mod.build(store=store, checkout=None)
    return hooks, calls


def _log(tmp_path):
    p = tmp_path / "coach" / "log.jsonl"
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


def _refused_browser(hooks, sid):
    hooks["post_tool_call"](tool_name="browser_exec", args={"code": "new_tab('http://10.0.0.7:18765/')"},
                            result=REFUSAL, session_id=sid, status="error")


def test_incident_block_is_held_once_then_passes(tmp_path, monkeypatch):
    hooks, calls = _hooks(tmp_path, monkeypatch)
    _refused_browser(hooks, "w1")
    args = {"task_id": "t_incident", "kind": "needs_input", "reason": INCIDENT_REASON,
            "human_request": INCIDENT_HR}
    out = hooks["pre_tool_call"](tool_name="kanban_block", args=args, session_id="w1")
    assert out and out["action"] == "block"
    assert "yourself" in out["message"] and "again" in out["message"]
    assert "Blocked: URL targets a private" in calls[0]   # the judge saw the turn's refused call
    held = [r for r in _log(tmp_path) if r.get("rule") == "block_handoff"]
    assert len(held) == 1 and held[0]["event"] == "agent_block" and held[0]["card"] == "t_incident"
    # the agent tried, still needs David: the same block goes through with no second judge call
    assert hooks["pre_tool_call"](tool_name="kanban_block", args=args, session_id="w1") is None
    assert len(calls) == 1
    assert [r["event"] for r in _log(tmp_path) if r.get("rule") == "block_handoff"] == [
        "agent_block", "block_handoff_repeat"]


def test_block_through_deferred_tool_call_and_kanban_create_with_human_request(tmp_path, monkeypatch):
    hooks, _ = _hooks(tmp_path, monkeypatch)
    wrapped = {"calls": [{"name": "kanban_block", "arguments": {"reason": INCIDENT_REASON,
                                                                "human_request": INCIDENT_HR}}]}
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_card")
    assert (hooks["pre_tool_call"](tool_name="tool_call", args=wrapped, session_id="w2") or {}).get("action") == "block"
    create = {"title": "Create the App", "initial_status": "blocked", "human_request": INCIDENT_HR}
    assert (hooks["pre_tool_call"](tool_name="kanban_create", args=create, session_id="w2") or {}).get(
        "action") == "block"


def test_genuine_passkey_ask_is_not_held(tmp_path, monkeypatch):
    hooks, calls = _hooks(tmp_path, monkeypatch)
    args = {"kind": "needs_input", "reason": PASSKEY_REASON,
            "human_request": {"action": "Approve the passkey prompt on your Mac. Unblock = continue; archive = drop.",
                              "why_human": "passkey", "interaction": "physical", "scope": "x", "evidence": "y"}}
    assert hooks["pre_tool_call"](tool_name="kanban_block", args=args, session_id="w3") is None
    assert len(calls) == 1


def test_non_human_blocks_and_off_switch_skip_the_judge(tmp_path, monkeypatch):
    hooks, calls = _hooks(tmp_path, monkeypatch)
    # a dependency/time park asks no human: no judge call
    assert hooks["pre_tool_call"](tool_name="kanban_block", args={"kind": "dependency", "reason": "waiting on t_x"},
                                  session_id="w4") is None
    assert hooks["pre_tool_call"](tool_name="kanban_create", args={"title": "child", "body": "do x"},
                                  session_id="w4") is None
    monkeypatch.setenv("COACH_BLOCK_TIME", "0")
    assert hooks["pre_tool_call"](tool_name="kanban_block", args={"kind": "needs_input", "reason": INCIDENT_REASON},
                                  session_id="w4") is None
    monkeypatch.delenv("COACH_BLOCK_TIME")
    monkeypatch.setenv("COACH_REPLY_RULES", "none")   # the handoff rule itself off: no block-time judge either
    assert hooks["pre_tool_call"](tool_name="kanban_block", args={"kind": "needs_input", "reason": INCIDENT_REASON},
                                  session_id="w4") is None
    assert calls == []


def test_judge_failure_fails_open(tmp_path, monkeypatch):
    hooks, _ = _hooks(tmp_path, monkeypatch)
    spec = importlib.util.spec_from_file_location("coach_init_bh2", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def boom(system, prompt):
        raise TimeoutError("judge down")

    monkeypatch.setattr(mod._load("reply_check"), "aux_judge", boom)
    assert hooks["pre_tool_call"](tool_name="kanban_block", args={"kind": "needs_input", "reason": INCIDENT_REASON},
                                  session_id="w5") is None
