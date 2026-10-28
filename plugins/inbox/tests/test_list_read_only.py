"""Desktop polls the inbox list once per profile: list() must open state.db read-only and close it
(a writable SessionDB runs schema init plus a system-wide open-file scan; core/backend-writable-open-guard.md)."""
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("inbox_store_ro", Path(__file__).resolve().parents[1] / "store.py")
store = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(store)


@pytest.fixture
def opens(tmp_path, monkeypatch):
    hermes_state = pytest.importorskip("hermes_state")  # imported per test, never at collection
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
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


@pytest.mark.parametrize("make", [lambda home: store.Inbox(home=home), lambda home: store.Inbox.for_home(home)],
                         ids=["own-home", "other-profile"])
def test_list_is_read_only_and_closed(tmp_path, opens, make):
    inbox = make(tmp_path)
    inbox.post("Approve deploy", "body", "Deploy it.", key="k1")
    assert inbox.exists()
    del opens[:]
    items = inbox.list()
    assert [i["title"] for i in items] == ["Approve deploy"]
    assert opens and all(d.read_only and d.closed for d in opens)
