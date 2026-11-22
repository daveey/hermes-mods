"""Real session membership capture closes before identity/projection work."""
import sqlite3

import pytest
from test_decisions import core, OWNER
from test_withdrawal import ROOT, load_module


def setup_api(tmp_path, monkeypatch):
    api = load_module('owner_capture_api', ROOT / 'dashboard' / 'plugin_api.py')
    monkeypatch.setattr(api, '_load', lambda name: core)
    monkeypatch.setattr(core, 'home', lambda: tmp_path)
    c = sqlite3.connect(tmp_path / 'state.db')
    c.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY,parent_session_id TEXT,end_reason TEXT,source TEXT)')
    c.executemany('INSERT INTO sessions(id) VALUES (?)', [('current',), ('parent',)])
    c.commit()
    c.close()
    return api


def test_panel_owner_capture_one_closed_query_only_snapshot(tmp_path, monkeypatch):
    api = setup_api(tmp_path, monkeypatch)
    original = core._ro_connect
    opened, identities = [], []

    def connect(*args, **kwargs):
        c = original(*args, **kwargs)
        opened.append(c)
        return c

    def identity(sid):
        assert len(opened) == 1
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            opened[0].execute('SELECT 1')
        identities.append(sid)
        return dict(OWNER, storedSessionId=sid)

    monkeypatch.setattr(core, '_ro_connect', connect)
    monkeypatch.setattr(core, 'server_owner', identity)
    owners = api.panel_owners(tmp_path, 'current', ['parent', 'missing', 'parent'])
    assert owners == [dict(OWNER, storedSessionId=s) for s in ('parent', 'current')]
    assert len(identities) == 1


def test_panel_owner_capture_distinguishes_missing_from_unreadable(tmp_path, monkeypatch):
    api = setup_api(tmp_path, monkeypatch)
    monkeypatch.setattr(core, 'server_owner', lambda sid: dict(OWNER, storedSessionId=sid))
    with pytest.raises(core.ActionError, match='Unknown stored session'):
        api.panel_owners(tmp_path, 'absent', ['parent'])
    c = sqlite3.connect(tmp_path / 'state.db')
    c.execute('DROP TABLE sessions')
    c.commit()
    c.close()
    with pytest.raises(sqlite3.OperationalError):
        api.panel_owners(tmp_path, 'current', ['parent'])
    with pytest.raises(core.ActionError, match='profile'):
        api.panel_owners(tmp_path / 'wrong', 'current', [])
