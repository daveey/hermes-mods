"""Comment syntax is reserved consistently with Gate G, across write paths."""
import shlex
import sqlite3

import pytest

from test_honest_done import plugin, core, make_board, OWN

BRANCH = "IF FAIL cpu: post the table, then request review."


def call(path, text):
    if path == "tool":
        return "kanban_comment", {"task_id": OWN, "body": text}
    if path == "cross":
        return "cross_task", {"action": "comment", "target": OWN, "message": text}
    if path == "wrapped":
        return "tool_call", {"calls": [{"name": "cross_task", "arguments": {
            "action": "comment", "target": OWN, "message": text}}]}
    return "terminal", {"command": f"hermes kanban comment {OWN} {shlex.quote(text)}"}


@pytest.fixture
def body(tmp_path, monkeypatch):
    db = make_board(tmp_path, tasks=[(OWN, "test", "running")])
    monkeypatch.setattr(core, "board_paths", lambda: [db])
    monkeypatch.setattr(plugin.comment_branches, "target_paths", lambda board: [db])
    monkeypatch.setattr(plugin, "classify_consent", lambda *a, **kw: None)
    monkeypatch.setattr(plugin, "_in_worker", lambda: False)
    def set_body(text):
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE tasks SET body=?", (text,))
    return set_body


@pytest.mark.parametrize("path", ["tool", "cross", "terminal", "wrapped"])
@pytest.mark.parametrize("text,registered,blocked", [
    (BRANCH, "", True),
    ("IF FAIL: post the table.", "", True),
    (BRANCH, BRANCH, False),
    ("Gate G said IF FAIL cpu: is missing.", "", False),
    ("Followed the body's branch:\n" + BRANCH, BRANCH, False),
    ("- If it failed: quote the failing test and diagnose it.", "", True),
    ("On failure, quote the failing test and diagnose it.", "", False),
    (BRANCH + " That branch is registered by this comment.", BRANCH, True),
])
def test_entry_matrix(body, path, text, registered, blocked):
    body(registered)
    name, args = call(path, text)
    result = plugin._pre_tool_call(name, args)
    assert bool(result) is blocked
    if blocked:
        assert result["action"] == "block"
        assert "An IF FAIL branch counts only in the card body" in result["message"]
        assert f"hermes kanban edit {OWN} --body" in result["message"]
        assert ("If you didn't mean to register a branch, write it as plain prose, for example "
                "'On failure, quote the failing test and diagnose it.'") in result["message"]


@pytest.mark.parametrize("path", ["tool", "cross", "terminal", "wrapped"])
def test_unreadable_allows_with_result_hint(body, monkeypatch, path):
    monkeypatch.setattr(plugin.comment_branches, "target_paths", lambda board: [])
    name, args = call(path, BRANCH)
    assert plugin._pre_tool_call(name, args) is None
    result = plugin._transform_tool_result(name, args, '{"ok":true}')
    assert result.startswith('{"ok":true}')
    assert "An IF FAIL branch counts only in the card body" in result


def test_readable_does_not_hint(body):
    body(BRANCH)
    name, args = call("tool", BRANCH)
    assert plugin._transform_tool_result(name, args, '{"ok":true}') is None


@pytest.mark.parametrize("options", ["--max-len 500", "--origin-gateway example", "--author worker",
                                    "--max-len=500 --author=worker"])
def test_cli_options(body, options):
    name, args = call("terminal", BRANCH)
    args["command"] = f"hermes kanban comment {options} {OWN} {shlex.quote(BRANCH)}"
    assert plugin._pre_tool_call(name, args)["action"] == "block"


def test_does_not_search_other_boards(body, monkeypatch):
    body(BRANCH)
    # The broad board inventory has a matching ID and branch, but target is unavailable.
    monkeypatch.setattr(plugin.comment_branches, "target_paths", lambda board: [])
    name, args = call("tool", BRANCH)
    assert plugin._pre_tool_call(name, args) is None
    assert plugin._transform_tool_result(name, args, "ok") is not None


@pytest.mark.parametrize("target", [f"@task:default@other/{OWN}", f"other:{OWN}"])
def test_foreign_target_never_uses_local_row(body, target):
    name, args = call("cross", BRANCH)
    args["target"] = target
    assert plugin._pre_tool_call(name, args) is None
    assert plugin._transform_tool_result(name, args, "ok") is not None


@pytest.mark.parametrize("path", ["tool", "cross", "terminal"])
def test_explicit_board_is_forwarded(body, monkeypatch, path):
    paths = plugin.comment_branches.target_paths(None)
    seen = []
    def resolve(board):
        seen.append(board)
        return paths
    monkeypatch.setattr(plugin.comment_branches, "target_paths", resolve)
    name, args = call(path, BRANCH)
    if path == "terminal":
        args["command"] = args["command"].replace("kanban comment", "kanban --board selected comment")
    elif path == "cross":
        args["target"] = f"@task:selected/{OWN}"
    else:
        args["board"] = "selected"
    assert plugin._pre_tool_call(name, args)["action"] == "block"
    assert seen == ["selected"]


def test_worker_is_guarded_too(body, monkeypatch):
    monkeypatch.setattr(plugin, "_in_worker", lambda: True)
    monkeypatch.setattr(core, "own_task_id", lambda: OWN)
    name, args = call("tool", BRANCH)
    assert plugin._pre_tool_call(name, args)["action"] == "block"

