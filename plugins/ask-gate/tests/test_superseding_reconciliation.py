"""Busy-board false positives and rewritten reconciliation, without live asks."""
import copy

import pytest

from test_superseding_work import (
    FILLER, MIGRATION_BODY, MIGRATION_TITLE, _args, _board, _home, hook,
)


def test_generic_execution_overlap_is_not_a_subject(hook, gate):
    _board(_home(gate) / "kanban.db", [
        {"id": "t_00000001", "title": "Orchid scorer deployment",
         "body": "CPU GPU parent acceptance run execution scientific readiness conditioning rerun envelope",
         "status": "running"},
        {"id": "t_00000002", "title": "Widget admission review",
         "body": "CPU GPU parent acceptance run execution scientific readiness conditioning rerun envelope",
         "status": "todo"},
    ])
    a = _args(title="Decision: CIFAR DC/MS conditioning rerun, one L40S, $36 envelope")
    a["human_request"].update(
        action="Approve $36 for CIFAR DC/MS rerun; retain parent scientific readiness and CPU acceptance.",
        scope="CIFAR conditioning experiment only, one GPU L40S. No execution before acceptance.")
    assert hook["pre_tool_call"](tool_name="kanban_create", args=a, session_id="s1") is None


@pytest.mark.parametrize("title, candidate_title", [
    ("Approval request", MIGRATION_TITLE),
    (None, "Infrastructure replacement"),
])
def test_distinctive_subject_in_contract_or_card_body_is_preserved(hook, gate, title, candidate_title):
    _board(_home(gate) / "kanban.db", FILLER + [
        dict(id="t_12345678", title=candidate_title, body=MIGRATION_BODY, status="blocked")])
    a = _args()
    if title:
        a["title"] = title
    held = hook["pre_tool_call"](tool_name="kanban_create", args=a, session_id="s1")
    assert held and "t_12345678" in held["message"]


@pytest.mark.parametrize("tool", ["kanban_create", "kanban_block"])
def test_rewritten_reconciliation_does_not_walk_next_six(hook, gate, tool):
    rows = [dict(id=f"t_{i:08x}", title=MIGRATION_TITLE, body=MIGRATION_BODY,
                 status="blocked") for i in range(16, 30)]
    _board(_home(gate) / "kanban.db", FILLER + rows)
    a = _args(idempotency_key="runner-decision", task_id="t_12345678")
    first = hook["pre_tool_call"](tool_name=tool, args=a, session_id="s1")
    assert first and first["action"] == "block"
    # Follow the gate's instruction, while shortening/rephrasing the request contract.
    b = copy.deepcopy(a)
    b["body"] += "\nRead and ruled out " + " ".join(r["id"] for r in rows[:6])
    b["human_request"]["action"] += " Retain the existing verification gate."
    b["human_request"]["scope"] += " No other fleet changes."
    assert hook["pre_tool_call"](tool_name=tool, args=b, session_id="s1") is None
    # Another independently identified filing is not a session-wide exemption.
    b["idempotency_key"] = "runner-decision-independent"
    b["task_id"] = "t_87654321"
    assert hook["pre_tool_call"](tool_name=tool, args=b, session_id="s1") is not None


def test_independent_approval_on_same_task_is_checked(hook, gate):
    _board(_home(gate) / "kanban.db", FILLER + [
        dict(id="t_12345678", title=MIGRATION_TITLE, body=MIGRATION_BODY, status="blocked")])
    a = _args(task_id="t_87654321")
    assert hook["pre_tool_call"](tool_name="kanban_block", args=a, session_id="s1") is not None
    b = copy.deepcopy(a)
    b["human_request"]["action"] = "Approve a separate EC2 spot runner experiment in the new VPC."
    b["human_request"]["scope"] = "EC2 runner AMI migration experiment only."
    assert hook["pre_tool_call"](tool_name="kanban_block", args=b, session_id="s1") is not None


@pytest.mark.parametrize("surface", ["kanban_create", "tool_call"])
def test_real_dispatch_reconciliation_and_independent_filing(hook, gate, monkeypatch, surface):
    import json
    import model_tools as mt
    import hermes_cli.plugins as plugins
    import tools.tool_search as ts
    import tools.kanban_tools  # register the real schema, never execute its writer

    rows = [dict(id=f"t_{i:08x}", title=MIGRATION_TITLE, body=MIGRATION_BODY,
                 status="blocked") for i in range(16, 30)]
    _board(_home(gate) / "kanban.db", FILLER + rows)
    entry = mt.registry.get_entry("kanban_create")
    assert entry is not None
    defs = [{"type": "function", "function": dict(entry.schema, name="kanban_create")}]
    monkeypatch.setattr(mt, "get_tool_definitions", lambda **kw: defs)
    monkeypatch.setattr(ts, "load_config_readonly", lambda: ts.ToolSearchConfig.from_raw({"defer": ["kanban_create"]}))
    seen, executed = [], []

    def dispatch(name, args, **kw):
        seen.append(name)
        out = hook["pre_tool_call"](tool_name=name, args=args, **kw)
        return (out["message"] if out else None), None

    monkeypatch.setattr(plugins, "_dispatch_pre_tool_call_hooks", dispatch)
    # The test may reach dispatch, but never the live board writer, post hooks or transforms.
    monkeypatch.setattr(mt, "_execute_tool", lambda name, args, *a, **kw: executed.append(name) or '{"fixture_executed": true}')
    monkeypatch.setattr(mt, "_emit_post_tool_call_hook", lambda **kw: None)
    monkeypatch.setattr(mt, "_apply_transform_tool_result_hook", lambda n, a, result, *rest: result)

    def call(args):
        payload = {"calls": [{"name": "kanban_create", "arguments": args}]} if surface == "tool_call" else args
        return json.loads(mt.handle_function_call(surface, payload, session_id="s1",
                          skip_tool_request_middleware=True, skip_tool_execution_middleware=True))

    a = _args(idempotency_key="dispatch-one")
    assert "superseding_work" in call(a)["error"]
    assert not executed
    b = copy.deepcopy(a)
    b["body"] += "\nRead and ruled out " + " ".join(r["id"] for r in rows[:6])
    b["human_request"]["action"] += " Retain scientific readiness."
    assert call(b) == {"fixture_executed": True}
    b["idempotency_key"] = "dispatch-two"
    assert "superseding_work" in call(b)["error"]
    assert executed == ["kanban_create"]
    assert seen == ["kanban_create"] * 3
