"""kanban-db-hint: a Kanban tool result that refuses a "corrupt" or unreliable board gets one actionable hint."""
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("kanban_db_hint_t", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.modules.setdefault("kanban_db_hint_t", mod)
    return mod


hint = _load()

# Verbatim kanban_show result from Desktop chat 20261027_210007_45baaa, 2026-10-27 23:33:53Z (state.db message 191294).
REAL_CORRUPT = json.dumps({"error": (
    "kanban_show: Refusing to open corrupt kanban DB at /home/ubuntu/.hermes/kanban.db: integrity_check returned "
    "'*** in database main ***\\nTree 13 page 13 cell 45: Rowid 1997 out of order'. Original preserved; backup at "
    "/home/ubuntu/.hermes/kanban.db.corrupt.a18094adddde6e53.bak.")})
# PR daveey/hermes#125 wording (KanbanDbUnreliableViewError), both variants.
PR125_STRANDED = json.dumps({"error": "kanban_show: kanban DB kanban.db: this process is attached to a deleted "
                                      "WAL/SHM generation (kanban.db-wal, kanban.db-shm); restart it (not corruption)"})
PR125_DISAGREE = json.dumps({"error": "kanban_list: kanban DB kanban.db: integrity probe disagrees with an "
                                      "independent process; transient, not quarantined"})


def test_real_error_gets_the_hint(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    out = hint.transform_tool_result(tool_name="kanban_show", args={"task_id": "t_x"}, result=REAL_CORRUPT)
    assert isinstance(out, str) and out != REAL_CORRUPT
    data = json.loads(out)
    assert data["error"] == json.loads(REAL_CORRUPT)["error"]  # the original error is kept verbatim
    h = data["hint"]
    assert h.startswith(hint.MARK)
    assert "sqlite3 -readonly /home/ubuntu/.hermes/kanban.db 'pragma quick_check'" in h
    assert "action_request" in h and "restart" in h
    assert "kanban_create" in h  # says why kanban_create from this chat will fail too
    assert "REINDEX" in h  # genuine corruption branch: don't repair the live file


def test_hint_names_configured_owner_cards(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "kanban-db-hint.json").write_text(json.dumps({"owner_cards": ["t_aaa", "t_bbb"]}))
    h = json.loads(hint.transform_tool_result(tool_name="kanban_show", args={}, result=REAL_CORRUPT))["hint"]
    assert "t_aaa" in h and "t_bbb" in h and "hermes kanban show t_aaa" in h


def test_pr125_wording_and_tool_call_wrapper(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    for res, tool in ((PR125_STRANDED, "kanban_show"), (PR125_DISAGREE, "tool_call")):
        h = json.loads(hint.transform_tool_result(tool_name=tool, args={}, result=res))["hint"]
        assert "pragma quick_check" in h and "<board db>" in h  # no path in the message: generic placeholder


def test_non_matching_results_unchanged(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    ok = json.dumps({"task": {"id": "t_x", "status": "done"}})
    other_err = json.dumps({"error": "kanban_show: task t_nope not found"})
    assert hint.transform_tool_result(tool_name="kanban_show", args={}, result=ok) is None
    assert hint.transform_tool_result(tool_name="kanban_show", args={}, result=other_err) is None
    # A terminal grep of logs that mentions the error is not a failing Kanban call.
    assert hint.transform_tool_result(tool_name="terminal", args={}, result=REAL_CORRUPT) is None
    assert hint.transform_tool_result(tool_name="kanban_show", args={}, result=None) is None


def test_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    once = hint.transform_tool_result(tool_name="kanban_show", args={}, result=REAL_CORRUPT)
    assert hint.transform_tool_result(tool_name="kanban_show", args={}, result=once) is None
    assert once.count(hint.MARK) == 1


def test_non_json_result_gets_appended_hint(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    raw = "Error: Refusing to open corrupt kanban DB at /x/kanban.db: bad. Original preserved; backup at /x/b."
    out = hint.transform_tool_result(tool_name="kanban_list", args={}, result=raw)
    assert out.startswith(raw) and hint.MARK in out and "sqlite3 -readonly /x/kanban.db" in out


def test_suggested_check_needs_no_approval():
    """The hint's check must not be the kind of command the approval filter stalls on (heredoc/python -c)."""
    cmd = hint.check_command("/home/ubuntu/.hermes/kanban.db")
    assert cmd == "sqlite3 -readonly /home/ubuntu/.hermes/kanban.db 'pragma quick_check'"
    assert "<<" not in cmd and "python" not in cmd
