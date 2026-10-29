"""Explicit asks are judged on the results their evidence cites; a vendor entitlement gap is not a missing credential.

Stub judges only: these prove what the judge is shown and how verdicts are applied, not real-model accuracy
(``scripts/replay_entitlement.py`` replays the same shapes through the real judge).
"""
import json

from conftest import llm_returning

INSTALL = json.dumps({"permissions": {"checks": "read", "issues": "write", "actions": "write", "contents": "write",
                                      "metadata": "read", "workflows": "write", "pull_requests": "write"},
                      "repository_selection": "all", "installation_id": 1001, "app_id": "2002"})
MINT_422 = ('{"output": "' + INSTALL.replace('"', '\\"') + '\\n{\\"status\\": 422, \\"error\\": {\\"message\\": '
            '\\"The permissions requested are not granted to this installation.\\"}}", "exit_code": 1}')


def entitlement_request(**changes):
    return {"action": "Choose: add Administration read to the shared App, or create a dedicated App",
            "why_human": "Changing a shared App's maximum permissions is an owner decision; no token can exceed it",
            "scope": "Installation 1001, repository example-repo only; no classic token fallback",
            "evidence": "mint administration:read for installation 1001 -> HTTP 422 permissions not granted",
            "interaction": "reply", **changes}


def block(gate, hr):
    return gate.pre_tool_call(tool_name="kanban_block", session_id="s1",
                              args={"kind": "needs_input", "reason": "owner decision", "human_request": hr})


def test_tail_of_a_long_result_survives_storage_and_rendering(gate):
    assert len(MINT_422) > 400 and MINT_422.index("422") > 220  # the old head-only cuts dropped it
    gate.LOG.add("s1", "terminal", {"command": "python3 mint.py installation 1001"}, MINT_422)
    stored = gate.LOG.get("s1")[0]["result"]
    assert "not granted to this installation" in stored
    assert "not granted to this installation" in gate.core.render_tools(gate.LOG.get("s1"))


def test_clip_keeps_head_and_tail_within_limit(gate):
    s = "HEAD" + "x" * 1000 + "TAIL"
    out = gate.core.clip(s, 100)
    assert len(out) <= 100 and out.startswith("HEAD") and out.endswith("TAIL")
    assert gate.core.clip("short", 100) == "short"


def test_explicit_request_judge_sees_the_cited_failure(gate):
    gate.LOG.add("s1", "terminal", {"command": "python3 mint.py installation 1001"}, MINT_422)
    stub = llm_returning([("ok", "entitlement gap shown by 422")])
    gate.LLM = stub
    assert block(gate, entitlement_request()) is None
    system, user = stub.seen[0]
    assert "TOOL CALLS THE REQUEST'S EVIDENCE CITES" in user
    assert "The permissions requested are not granted to this installation." in user
    assert "ENTITLEMENT" in system and "no API" in system


def test_uncited_calls_are_not_expanded(gate):
    gate.LOG.add("s1", "read_file", {"path": "/tmp/notes.md"}, "z" * 1200 + " SECRETLESS-TAIL-MARK")
    gate.LOG.add("s1", "terminal", {"command": "python3 mint.py installation 1001"}, MINT_422)
    stub = llm_returning([("ok", "fine")])
    gate.LLM = stub
    block(gate, entitlement_request())
    cited = stub.seen[0][1].split("TOOL CALLS THE REQUEST'S EVIDENCE CITES", 1)[1]
    assert "mint.py" in cited and "notes.md" not in cited


def test_invented_scope_does_not_refuse_an_entitlement_request(gate):
    """agent_can_do naming a scope that is neither on the sheet nor used this turn is speculation."""
    gate.LOG.add("s1", "terminal", {"command": "python3 mint.py installation 1001"}, MINT_422)
    gate.LLM = llm_returning([("agent_can_do", "a minter path exists", "token-broker exec --scope user/github-minter")])
    assert block(gate, entitlement_request()) is None


def test_missing_credential_with_a_real_minter_is_still_refused(gate):
    """The sheet's cf-token-minter can issue the permission: the gate keeps refusing that ask."""
    gate.LOG.add("s1", "terminal", {"command": "curl email/routing with cf-infra"}, "HTTP 403 Authentication error")
    gate.LLM = llm_returning([("agent_can_do", "minter on sheet", "token-broker exec --scope cf-token-minter")])
    r = block(gate, {"action": "Add Email Routing to the cf-infra token in the dashboard",
                     "why_human": "Only David edits tokens", "scope": "one zone",
                     "evidence": "curl email/routing with cf-infra -> HTTP 403 Authentication error",
                     "interaction": "reply"})
    assert r and r["action"] == "block" and "(agent_can_do)" in r["message"]


def test_route_used_this_turn_counts_as_grounded(gate):
    """A scope absent from the sheet but used in this turn's tool calls is a real route, so it still refuses."""
    gate.LOG.add("s1", "terminal", {"command": "token-broker exec --scope user/app-key-holder -- python3 check.py"},
                 "installation 1001 permissions: administration read")
    gate.LLM = llm_returning([("agent_can_do", "App already has it", "token-broker exec --scope user/app-key-holder")])
    r = block(gate, entitlement_request(evidence="check.py installation 1001 permissions administration read"))
    assert r and r["action"] == "block"


def test_unverified_verdict_still_refuses(gate):
    gate.LOG.add("s1", "terminal", {"command": "python3 mint.py installation 1001"}, MINT_422)
    gate.LLM = llm_returning([("unverified", "premise unchecked", "GET the installation permissions first")])
    r = block(gate, entitlement_request())
    assert r and r["action"] == "block" and "(unverified)" in r["message"]
