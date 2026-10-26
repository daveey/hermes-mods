"""Qualified closeout ownership; every database is a temporary SQLite fixture."""
import sqlite3
import sys
import types

import pytest
from test_closeout import plugin, core


def board(path, status='done'):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE tasks (id TEXT, title TEXT, status TEXT)')
        db.execute('INSERT INTO tasks VALUES (?,?,?)', ('t_same', 'Local task', status))
    return path


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    local = board(tmp_path / 'kanban.db')
    rows = [{'id': 't_same', 'key': 'owner/work/build/t_same', 'gateway': 'owner',
             'profile': 'work', 'board': 'build', 'remote': True, 'status': 'unknown',
             'unfinished': True, 'stale': True, 'via': 'origin', 'binding_id': 'binding-001'}]
    activity = types.SimpleNamespace(LISTED_STATUSES=('running', 'unknown'),
                                     session_tasks=lambda *args: list(rows))
    monkeypatch.setitem(sys.modules, 'hermes_cli', types.SimpleNamespace(kanban_session_activity=activity))
    monkeypatch.setattr(plugin, 'board_paths', lambda: [local])
    return rows, local


def test_unknown_remote_collision_never_settles(environment, tmp_path, monkeypatch):
    rows, _ = environment
    monkeypatch.setattr(plugin, 'remote_cards', lambda: rows, raising=False)
    ids = plugin.open_card_ids('chat')
    led = core.Ledger(tmp_path / 'closeout.db')
    core.record_turn_end(led, 'chat', ids, plugin.read_cards)
    assert core.closeout_key(led.get('chat'), plugin.read_cards(ids)) is None
    assert ids == ['owner/work/build/t_same']
    assert not led.get('chat')['covered']


@pytest.mark.parametrize('status,stale,unfinished,settled', [
    ('done', False, False, True), ('done', True, False, False),
    ('done', False, True, False), ('unknown', False, False, False),
    ('running', False, True, False), ('review', False, True, False),
    ('blocked', False, True, True), ('blocked', True, True, False),
])
def test_remote_evidence_controls_settlement(environment, monkeypatch, status, stale, unfinished, settled):
    rows, _ = environment
    rows[0].update(status=status, stale=stale, unfinished=unfinished, block_kind='needs_input')
    monkeypatch.setattr(plugin, 'remote_cards', lambda: rows, raising=False)
    ids = ['owner/work/build/t_same']
    assert bool(core.closeout_key({'batch': ids}, plugin.read_cards(ids))) is settled


def test_local_board_collision_and_legacy_ambiguity(tmp_path):
    a = board(tmp_path / 'kanban.db')
    b = board(tmp_path / 'kanban' / 'boards' / 'work' / 'kanban.db', 'running')
    cards = core.read_cards([a, b], ['default/t_same', 'work/t_same', 't_same'])
    assert cards['default/t_same']['status'] == 'done'
    assert cards['work/t_same']['status'] == 'running'
    assert 't_same' not in cards  # old unqualified ledger must not pick the first match
    assert core.read_cards([a], ['t_same'])['t_same']['status'] == 'done'


def test_legacy_remote_collision_is_unknown(environment, monkeypatch):
    rows, _ = environment
    monkeypatch.setattr(plugin, 'remote_cards', lambda: rows, raising=False)
    assert not core.closeout_key({'batch': ['t_same']}, plugin.read_cards(['t_same']))


def test_two_remote_profiles_not_conflated(environment, monkeypatch):
    rows, _ = environment
    rows.append({**rows[0], 'key': 'owner/other/build/t_same', 'profile': 'other',
                 'status': 'done', 'stale': False, 'unfinished': False})
    monkeypatch.setattr(plugin, 'remote_cards', lambda: rows, raising=False)
    ids = plugin.open_card_ids('chat')
    assert len(ids) == 2
    assert core.closeout_key({'batch': ids}, plugin.read_cards(ids)) is None


def test_unavailable_projection_preserves_qualified_local(environment, monkeypatch):
    def unavailable():
        raise OSError('unavailable')
    monkeypatch.setattr(plugin, 'remote_cards', unavailable)
    cards = plugin.read_cards(['t_same', 'default/t_same', 'owner/work/build/t_same'])
    assert set(cards) == {'default/t_same'}
    assert core.is_settled(cards['default/t_same'])


def test_stale_done_cannot_discard_covered_remote(environment, monkeypatch):
    rows, _ = environment
    rows[0].update(status='done', stale=True, unfinished=False)
    key = rows[0]['key']
    fix = core.reconcile({'batch': [key], 'covered': [key]}, {key: rows[0]})
    assert 'batch' not in fix
    assert fix['covered'] == set()


def test_worker_pin_still_skips_hooks(monkeypatch):
    monkeypatch.setenv('HERMES_KANBAN_TASK', 't_fixture')
    assert not plugin._eligible('chat', 'cli')
