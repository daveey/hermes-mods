"""Watchdog side of a deleted chat: its never-deliverable unseen notices and settled batch raise no incident, and
an incident already filed for it resolves with that evidence; a live chat is still flagged."""
import importlib.util
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('closeout_plugin_deleted_chat_watchdog', ROOT / '__init__.py')
plugin = importlib.util.module_from_spec(spec)
plugin.__package__ = ''
spec.loader.exec_module(plugin)
core = plugin.core

GONE = '20261103_014034_52a789'
LIVE = '20261103_014500_abcdef'


def _state_db(path: Path, ids=(LIVE,)) -> Path:
    c = sqlite3.connect(path)
    c.execute('CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, archived INTEGER, end_reason TEXT)')
    for sid in ids:
        c.execute("INSERT INTO sessions (id, source) VALUES (?, 'desktop')", (sid,))
    c.commit()
    c.close()
    return path


def _setup(tmp_path, monkeypatch, dbs=None):
    led = core.Ledger(tmp_path / 'ledger.db')
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    paths = dbs if dbs is not None else [_state_db(tmp_path / 'state.db')]
    monkeypatch.setattr(plugin, 'state_dbs', lambda: paths)
    monkeypatch.setattr(plugin, '_compression_tip', lambda sid: None)
    monkeypatch.setattr(plugin, '_lineage', lambda sid: [sid])
    monkeypatch.setitem(plugin._state, 'mono_seen', {})
    return led



def test_watchdog_never_flags_a_deleted_chat_but_still_flags_a_live_one(tmp_path, monkeypatch):
    led = _setup(tmp_path, monkeypatch)
    cards = {'t_one': {'status': 'done'}}
    sig = core.closeout_key({'batch': ['t_one']}, cards)
    for sid in (GONE, LIVE):
        led.write(sid, batch=['t_one'], open=[], settled_sig=sig, settled_at=1.0)
    posts = []
    args = dict(unseen={GONE: 1, LIVE: 1}, post=lambda *a: posts.append(a), force=True, read=lambda _: cards)
    plugin.watchdog_tick(now=2, mono=2, **args)
    raised = plugin.watchdog_tick(now=1900, mono=1900, **args)
    assert raised and all(GONE not in k for k in raised)
    assert any(k.startswith(f'chat-closeout:unseen:{LIVE}:') for k in raised)
    assert f'chat-closeout:no-closeout:{LIVE}:{sig}' in raised


def test_cleared_evidence_names_the_deletion(tmp_path, monkeypatch):
    led = _setup(tmp_path, monkeypatch)
    ev = plugin._cleared_evidence(f'chat-closeout:unseen:{GONE}:1', {GONE: 1}, led)
    assert 'deleted' in ev
    assert 'deleted' not in plugin._cleared_evidence(f'chat-closeout:unseen:{LIVE}:1', {LIVE: 0}, led)
