"""Session-row reads open state.db read-only and close it (a writable SessionDB runs schema init plus a
system-wide open-file scan; core/backend-writable-open-guard.md)."""
import importlib.util
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("user_terminal_ro", Path(__file__).resolve().parents[1] / "__init__.py")
plugin = importlib.util.module_from_spec(_spec)
sys.modules["user_terminal_ro"] = plugin
_spec.loader.exec_module(plugin)


@pytest.fixture
def opens(tmp_path, monkeypatch):
    hermes_state = pytest.importorskip("hermes_state")  # imported per test, never at collection
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    db = hermes_state.SessionDB(db_path=tmp_path / "state.db")
    db.create_session("s1", source="slack")
    db.set_session_title("s1", "Weekly sync")
    db.close()
    real = hermes_state.SessionDB
    seen = []

    class Spy(real):
        def __init__(self, db_path=None, read_only=False):
            super().__init__(db_path=db_path or tmp_path / "state.db", read_only=read_only)
            self.closed = False
            seen.append(self)

        def close(self):
            self.closed = True
            super().close()

    monkeypatch.setattr(hermes_state, "SessionDB", Spy)
    return seen


def test_sender_title_is_read_only(opens):
    assert plugin._sender("s1")["title"] == "Weekly sync"
    assert opens and all(d.read_only and d.closed for d in opens)


def test_caller_surfaces_session_source_is_read_only(opens):
    assert "slack" in plugin.caller_surfaces("s1")
    assert [(d.read_only, d.closed) for d in opens] == [(True, True)]
