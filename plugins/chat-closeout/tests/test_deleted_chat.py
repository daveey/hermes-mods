"""A ledger row whose chat was deleted (throwaway test chat) is dropped, never woken or flagged forever."""
import importlib.util
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('closeout_deleted', ROOT / '__init__.py')
plugin = importlib.util.module_from_spec(spec)
plugin.__package__ = ''
spec.loader.exec_module(plugin)
core = plugin.core

SID = '20261103_014034_52a789'


def state_db(path: Path, ids):
    c = sqlite3.connect(path)
    c.execute('CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT)')
    c.executemany('INSERT INTO sessions VALUES (?, ?)', [(i, 'desktop') for i in ids])
    c.commit()
    c.close()
    return path


def setup(tmp_path, monkeypatch, dbs, sid=SID):
    led = core.Ledger(tmp_path / 'ledger.db')
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setattr(plugin, '_lineage', lambda s: [s])
    monkeypatch.setattr(plugin, '_moved_owner', lambda s: None, raising=False)
    monkeypatch.setattr(plugin, 'state_dbs', lambda: dbs)
    cards = {'t1': {'status': 'done'}}
    led.write(sid, batch=list(cards), open=[])
    calls = []

    def tick(now):
        return plugin.wake_tick(now=now, wake=lambda *a, **k: calls.append(a) or 'started', unseen={},
                                last_user=lambda _: 0, read=lambda _: cards, proof=lambda *a: True)
    return led, calls, tick


def test_deleted_chat_row_is_dropped_never_woken(tmp_path, monkeypatch):
    db = state_db(tmp_path / 'state.db', ['20261101_000000_abcdef'])
    led, calls, tick = setup(tmp_path, monkeypatch, [db])
    tick(1000)
    tick(5000)
    assert calls == []
    assert led.pending() == []


def test_present_chat_is_not_dropped(tmp_path, monkeypatch):
    db = state_db(tmp_path / 'state.db', [SID])
    monkeypatch.setattr(plugin, 'state_dbs', lambda: [db])
    assert plugin._session_gone(SID) is False


def test_chat_in_profile_store_is_not_gone(tmp_path, monkeypatch):
    a = state_db(tmp_path / 'a.db', [])
    b = state_db(tmp_path / 'b.db', [SID])
    monkeypatch.setattr(plugin, 'state_dbs', lambda: [a, b])
    assert plugin._session_gone(SID) is False


def test_unreadable_store_is_unknown_not_gone(tmp_path, monkeypatch):
    good = state_db(tmp_path / 'a.db', [])
    bad = tmp_path / 'bad.db'
    bad.write_text('not a database')
    monkeypatch.setattr(plugin, 'state_dbs', lambda: [good, bad])
    assert plugin._session_gone(SID) is False


def test_no_stores_or_non_hermes_ids_never_gone(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin, 'state_dbs', lambda: [])
    assert plugin._session_gone(SID) is False
    db = state_db(tmp_path / 'state.db', [])
    monkeypatch.setattr(plugin, 'state_dbs', lambda: [db])
    for sid in ('chat', 'default/' + SID, 'hub:' + SID, ''):
        assert plugin._session_gone(sid) is False
    assert plugin._session_gone(SID) is True


LOCAL_SID = 'local-' + 'b' * 64


def test_deleted_desktop_local_chat_is_gone(tmp_path, monkeypatch):
    """Desktop chats carry runtime-minted local-<sha256> ids; a deleted one must count as gone."""
    db = state_db(tmp_path / 'state.db', [SID])
    monkeypatch.setattr(plugin, 'state_dbs', lambda: [db])
    assert plugin._session_gone(LOCAL_SID) is True
    db2 = state_db(tmp_path / 'b.db', [LOCAL_SID])
    monkeypatch.setattr(plugin, 'state_dbs', lambda: [db, db2])
    assert plugin._session_gone(LOCAL_SID) is False
    for sid in ('local-' + 'b' * 16, 'local-xyz', 'hub:' + LOCAL_SID):
        assert plugin._session_gone(sid) is False
