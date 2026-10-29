"""Exercise the registered pre-tool boundary, without models or live systems."""
from copy import deepcopy
from types import SimpleNamespace

import pytest


@pytest.fixture
def hook(gate, monkeypatch):
    monkeypatch.setattr(gate.runtime_contract, "require_runtime_contract", lambda: None)
    monkeypatch.setattr(gate.approval_adapter_loading, "install", lambda *a: None)
    monkeypatch.setattr(gate.lifecycle, "preflight", lambda *a: None)
    monkeypatch.setattr(gate.approval_reconciliation, "preflight", lambda *a: None)
    monkeypatch.setattr(gate, "_retired_block", lambda *a: None)
    monkeypatch.setattr(gate.core, "checked_matches", lambda *a: True)
    monkeypatch.setattr(gate, "_judge_explicit", lambda *a: [{"i": 0, "verdict": "ok"}])
    hooks = {}
    gate.register(SimpleNamespace(register_hook=lambda name, cb: hooks.update({name: cb})))
    return hooks["pre_tool_call"]


def args_for(tool, action, scope="example/widget PR #1950, head 21cb9c1c2 only"):
    return {"initial_status": "blocked", "kind": "human", "block_kind": "human",
            "title": "Review change", "human_request": {
                "action": action, "why_human": "Owner review required", "scope": scope,
                "evidence": "Checked: gh pr view — open", "interaction": "approval"}}


@pytest.mark.parametrize("tool", ["kanban_block", "kanban_create"])
def test_malformed_held_once_and_canonical_filled(hook, gate, tool):
    args = args_for(tool, "Approve PR #1950 at head 21cb9c1c2 and rotate the deploy key")
    original = deepcopy(args)
    held = hook(tool_name=tool, args=args, session_id="s1")
    assert args == original
    assert held and held["action"] == "block"
    assert "Approve https://github.com/example/widget/pull/1950 (head 21cb9c1c2) on GitHub" in held["message"]
    assert "Unblock =" in held["message"] and "archive =" in held["message"]
    gate.LOG.reset("s1")  # next-turn tool log reset must not reset the one-shot lint
    assert hook(tool_name=tool, args=args, session_id="s1") is None
    assert hook(tool_name=tool, args=args, session_id="other") is not None


@pytest.mark.parametrize("tool", ["kanban_block", "kanban_create"])
def test_canonical_allowed(hook, tool):
    action = ("Approve https://github.com/example/widget/pull/1950 (head 21cb9c1c2) on GitHub, "
              "or OK the bb approval ask in Discord, Slack or the BB Inbox. "
              "Unblock = queue merges+deploys and I verify; archive = close the PR, findings stay open.")
    assert hook(tool_name=tool, args=args_for(tool, action), session_id="s1") is None


@pytest.mark.parametrize("tool", ["kanban_block", "kanban_create"])
def test_missing_fields_explicit(hook, tool):
    held = hook(tool_name=tool, args=args_for(tool, "OK PR #1950 and proceed", scope="This change"), session_id="s1")
    assert held and "OWNER/REPO" in held["message"] and "SHA" in held["message"]
    assert "Missing" in held["message"]


def test_hold_key_includes_tool_and_ask(hook):
    args = args_for("kanban_block", "Approve PR #1950 and deploy")
    assert hook(tool_name="kanban_block", args=args, session_id="s1") is not None
    assert hook(tool_name="kanban_create", args=args, session_id="s1") is not None
    args["human_request"]["action"] = "Approve PR #1950 and rotate key"
    assert hook(tool_name="kanban_block", args=args, session_id="s1") is not None


@pytest.mark.parametrize("action", ["Restart the service", "Approve the budget"])
def test_unrelated_request_unchanged(hook, action):
    assert hook(tool_name="kanban_block", args=args_for("kanban_block", action), session_id="s1") is None


@pytest.mark.parametrize("tool", ["kanban_block", "kanban_create"])
def test_reported_surface_regression_allowed(hook, tool):
    action = ("Approve PR #1950 at head 21cb9c1c2 on GitHub, or OK the bb approval ask in "
              "Slack/Discord/BB Inbox. Unblock = queue merges+deploys and I verify; "
              "archive = close the PR, findings stay open.")
    assert hook(tool_name=tool, args=args_for(tool, action), session_id="s1") is None


@pytest.mark.parametrize("tool", ["kanban_block", "kanban_create"])
def test_first_hold_has_no_judge_and_retry_keeps_evidence_check(hook, gate, monkeypatch, tool):
    def no_judge(*a):
        raise AssertionError("wording hold must not call the judge")
    monkeypatch.setattr(gate, "_judge_explicit", no_judge)
    args = args_for(tool, "Approve PR #1950 and rotate key")
    held = hook(tool_name=tool, args=args, session_id="s1")
    assert held and "wording held once" in held["message"]
    monkeypatch.setattr(gate.core, "checked_matches", lambda *a: False)
    retried = hook(tool_name=tool, args=args, session_id="s1")
    assert retried and "matches no tool call" in retried["message"]
    assert "wording held once" not in retried["message"]


def test_nonblocked_create_uses_existing_validation(hook):
    args = args_for("kanban_create", "Approve PR #1950 and rotate key")
    args["initial_status"] = "ready"
    held = hook(tool_name="kanban_create", args=args, session_id="s1")
    assert "initial_status=blocked" in held["message"]


@pytest.mark.parametrize('tool', ['kanban_block', 'kanban_create'])
@pytest.mark.parametrize('outcome', ['Unblock', 'archive'])
def test_unknown_outcome_hold_names_clause(hook, tool, outcome):
    unblock = 'merge and wipe the prod bucket' if outcome == 'Unblock' else 'merge'
    archive = 'close the PR then email credentials' if outcome == 'archive' else 'close the PR'
    action = f'Approve PR #1950 head 21cb9c1c2. Unblock = {unblock}; archive = {archive}.'
    held = hook(tool_name=tool, args=args_for(tool, action), session_id='s1')
    assert held and held['action'] == 'block'
    assert f'Unrecognised {outcome} outcome clause' in held['message']
    assert ('wipe the prod bucket' if outcome == 'Unblock' else 'email credentials') in held['message']


@pytest.mark.parametrize('tool', ['kanban_block', 'kanban_create'])
def test_scope_instruction_hold_names_text(hook, tool):
    scope = 'example/widget PR #1950, head 21cb9c1c2; also rotate the prod keys'
    args = args_for(tool, 'Approve PR #1950 head 21cb9c1c2. Unblock = merge; archive = close the PR.', scope)
    held = hook(tool_name=tool, args=args, session_id='scope-test')
    assert held and held['action'] == 'block'
    assert 'Unrecognised scope text' in held['message']
    assert scope in held['message']
