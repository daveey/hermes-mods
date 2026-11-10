"""workflow-state-hint: a tool result naming a card of an unfinished/errored project-work workflow gets one line
saying the workflow's real state, so a card moving to review is not reported as the workflow recovering.

Regression for self-heal defect agent-55c981ebe2: on 2026-11-02 an agent told the user "the blocked builder has
now recovered" because card t_01a19a6b4fcce737 moved blocked -> review, while its Ask Eve workflow 2dca7d56 still
had ``WorkflowError: All exact native cards must be done`` and had published nothing.
"""
import importlib.util
import json
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("workflow_state_hint_t", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return sys.modules.setdefault("workflow_state_hint_t", mod)


hint = _load()

ERR_WF = "2dca7d56-8e8a-4fde-badc-610acd2163c5"
ERR_CARD = "t_01a19a6b4fcce737"
DONE_WF = "11111111-2222-3333-4444-555555555555"
DONE_CARD = "t_0aaaaaaaaaaaaaaa"
OTHER_CARD = "t_0bbbbbbbbbbbbbbb"


def _ledger(home: Path, profile: str = "alpha-askeve") -> Path:
    """Same schema as plugins/project-work/store.py, with the incident's workflow shape."""
    p = home / "profiles" / profile / "project-work" / "ledger.sqlite3"
    p.parent.mkdir(parents=True)
    c = sqlite3.connect(p)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""CREATE TABLE workflows (
                    workflow_id TEXT PRIMARY KEY, team_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL, thread_id TEXT NOT NULL,
                    data TEXT NOT NULL, UNIQUE(team_id, channel_id, thread_id))""")
    errored = {"workflow_id": ERR_WF, "state": "running", "error": "WorkflowError: All exact native cards must be done",
               "result_receipt": None, "title": "Ask Eve change",
               "cards": {"implement": {"id": ERR_CARD, "state": "created", "recovered_from": "create_intent"},
                         "review": {"id": "t_01a19a6bca672d6a", "state": "created"},
                         "release": {"id": "t_01a19a6bcbee04ea", "state": "created"}}}
    done = {"workflow_id": DONE_WF, "state": "posted", "error": None, "result_receipt": {"ts": "1"},
            "cards": {"implement": {"id": DONE_CARD, "state": "created"}}}
    for i, d in enumerate((errored, done)):
        c.execute("insert into workflows values (?,?,?,?,?)", (d["workflow_id"], "T", "C", str(i), json.dumps(d)))
    c.commit()
    c.close()
    return p


def _show(card: str, status: str = "review") -> str:
    return json.dumps({"task": {"id": card, "status": status, "title": "implement"}})


def test_errored_workflow_card_gets_hint(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _ledger(tmp_path)
    out = hint.transform_tool_result(tool_name="kanban_show", args={"task_id": ERR_CARD}, result=_show(ERR_CARD))
    assert isinstance(out, str)
    data = json.loads(out)
    assert data["task"]["id"] == ERR_CARD  # original result kept
    line = data["workflow_state"]
    assert line.startswith("[workflow-state]")
    assert ERR_CARD in line and ERR_WF in line
    assert "state=running" in line and "All exact native cards must be done" in line and "published=no" in line
    assert "not workflow recovery" in line


def test_terminal_cli_output_gets_hint(monkeypatch, tmp_path):
    """The live path: `hermes kanban --board askeve show <card> --json` run through the terminal tool."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _ledger(tmp_path)
    res = json.dumps({"output": _show(ERR_CARD), "exit_code": 0, "error": None})
    out = hint.transform_tool_result(tool_name="terminal", args={"command": "hermes kanban show"}, result=res)
    assert "[workflow-state]" in json.loads(out)["workflow_state"]
    # plain-text results get the line appended at the end
    out2 = hint.transform_tool_result(tool_name="mcp__execute_code", args={}, result=f"card {ERR_CARD} is review")
    assert out2.rstrip().splitlines()[-1].startswith("[workflow-state]")


def test_profile_home_reads_its_own_ledger(monkeypatch, tmp_path):
    _ledger(tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profiles" / "alpha-askeve"))
    out = hint.transform_tool_result(tool_name="tool_call", args={}, result=_show(ERR_CARD))
    assert ERR_WF in json.loads(out)["workflow_state"]


def test_clean_finished_workflow_no_hint(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _ledger(tmp_path)
    assert hint.transform_tool_result(tool_name="kanban_show", args={}, result=_show(DONE_CARD, "done")) is None


def test_unrelated_card_no_hint(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _ledger(tmp_path)
    assert hint.transform_tool_result(tool_name="kanban_show", args={}, result=_show(OTHER_CARD)) is None


def test_other_tools_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _ledger(tmp_path)
    assert hint.transform_tool_result(tool_name="read_file", args={}, result=_show(ERR_CARD)) is None


def test_missing_ledger_passthrough(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert hint.transform_tool_result(tool_name="kanban_show", args={}, result=_show(ERR_CARD)) is None
    assert not (tmp_path / "profiles").exists()  # never creates a ledger


def test_locked_ledger_passthrough_within_budget(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    p = _ledger(tmp_path)
    c = sqlite3.connect(p)
    c.execute("PRAGMA journal_mode=DELETE")  # rollback journal: an exclusive lock blocks readers
    c.execute("BEGIN EXCLUSIVE")
    try:
        t0 = time.monotonic()
        out = hint.transform_tool_result(tool_name="kanban_show", args={}, result=_show(ERR_CARD))
        assert out is None
        assert time.monotonic() - t0 < 1.0
    finally:
        c.rollback()
        c.close()


def test_corrupt_ledger_passthrough(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    p = tmp_path / "profiles" / "x" / "project-work" / "ledger.sqlite3"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"not a database")
    assert hint.transform_tool_result(tool_name="kanban_show", args={}, result=_show(ERR_CARD)) is None


def test_at_most_three_workflows(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    p = tmp_path / "project-work" / "ledger.sqlite3"
    p.parent.mkdir(parents=True)
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE workflows (workflow_id TEXT PRIMARY KEY, team_id TEXT, channel_id TEXT, thread_id TEXT,"
              " data TEXT)")
    cards = []
    for i in range(5):
        card = f"t_0c{i:014d}"
        cards.append(card)
        d = {"workflow_id": f"wf{i}", "state": "running", "error": "E", "cards": {"implement": {"id": card}}}
        c.execute("insert into workflows values (?,?,?,?,?)", (f"wf{i}", "T", "C", str(i), json.dumps(d)))
    c.commit()
    c.close()
    out = hint.transform_tool_result(tool_name="kanban_list", args={}, result=json.dumps({"tasks": cards}))
    assert json.loads(out)["workflow_state"].count("[workflow-state]") == 3


def test_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _ledger(tmp_path)
    once = hint.transform_tool_result(tool_name="kanban_show", args={}, result=_show(ERR_CARD))
    assert hint.transform_tool_result(tool_name="kanban_show", args={}, result=once) is None


def test_ledger_is_never_written(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    p = _ledger(tmp_path)
    before = p.read_bytes()
    hint.transform_tool_result(tool_name="kanban_show", args={}, result=_show(ERR_CARD))
    assert p.read_bytes() == before


def test_register_hooks_transform_tool_result():
    seen = []

    class Ctx:
        def register_hook(self, name, fn):
            seen.append(name)

    hint.register(Ctx())
    assert seen == ["transform_tool_result"]
