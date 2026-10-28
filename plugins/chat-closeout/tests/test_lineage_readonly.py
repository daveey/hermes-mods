"""Lineage lookups run once per finished card on every sidebar poll: they must open state.db read-only and close it.

A writable SessionDB() per lookup (schema init + system-wide open-file scan, never closed) held the Desktop backend
near 100% CPU; see core/backend-writable-open-guard.md."""
import pytest
from test_closeout import plugin

hermes_state = pytest.importorskip('hermes_state')


@pytest.fixture
def tracked(tmp_path, monkeypatch):
    """A real state.db with a compressed chat (root -> tip), plus a record of every SessionDB open/close."""
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    db = hermes_state.SessionDB(db_path=tmp_path / 'state.db')
    db.create_session('root', source='cli')
    db.end_session('root', 'compression')
    db.create_session('tip', source='cli', parent_session_id='root')
    db.close()
    real = hermes_state.SessionDB
    opens = []

    class Spy(real):
        def __init__(self, db_path=None, read_only=False):
            super().__init__(db_path=db_path or tmp_path / 'state.db', read_only=read_only)
            self.closed = False
            opens.append(self)

        def close(self):
            self.closed = True
            super().close()

    monkeypatch.setattr(hermes_state, 'SessionDB', Spy)
    return opens


def test_lineage_follows_compression_with_a_closed_read_only_open(tracked):
    assert plugin._lineage('root') == ['root', 'tip']
    assert [(d.read_only, d.closed) for d in tracked] == [(True, True)]


def test_owner_wake_tip_lookup_is_read_only_and_closed(tracked):
    assert plugin._compression_tip('root') == 'tip'
    assert plugin._compression_tip('nope') in (None, 'nope')
    assert all(d.read_only and d.closed for d in tracked) and len(tracked) == 2
