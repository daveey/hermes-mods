import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / filename)
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


bt = module('board_task_tests', 'board_tasks.py')


def test_headless_entry_does_not_use_desktop(monkeypatch):
    sm = module('headless_sm', '__init__.py')
    monkeypatch.setattr(sm, 'bt', SimpleNamespace(enabled=lambda: True, handle=lambda args: {'ok': True, 'source': 'replica'}), raising=False)
    monkeypatch.setattr(sm, '_relay', lambda *a, **kw: pytest.fail('Desktop relay used'))
    assert json.loads(sm.handle_task({'action': 'read', 'target': 't_123'}))['source'] == 'replica'


@pytest.mark.parametrize('target', ['@task:../secret/t_123', '@task:default@gw/t_1/extra', 't_123;delete'])
def test_invalid_targets(target):
    with pytest.raises(ValueError):
        bt.target_parts({'target': target})


def test_conflicting_selectors():
    with pytest.raises(ValueError):
        bt.target_parts({'target': '@task:default@gw/t_123', 'board': 'other'})
    assert bt.target_parts({'target': '@task:default@gw/t_123'}) == ('default', 'gw', 't_123')


@pytest.fixture
def replica(tmp_path, monkeypatch):
    kb = pytest.importorskip('hermes_cli.kanban_db')
    from hermes_cli import kanban_db_connect as kbc, kanban_db_sync as sync
    from hermes_cli import kanban_foreign_card as fc
    from tools import kanban_tools as kt
    from contextlib import contextmanager
    import sqlite3
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    live_path = tmp_path / 'kanban.db'
    shadow_path = tmp_path / 'shadow.db'
    kbc.init_db(shadow_path)
    with sqlite3.connect(shadow_path) as conn:
        conn.execute('ALTER TABLE tasks ADD COLUMN created_on TEXT')
        conn.execute("INSERT INTO tasks(id,title,body,assignee,status,created_at,created_on) VALUES ('t_123','parent','body','default','done',1,'owner')")
    @contextmanager
    def granted(board, tid):
        if board not in (None, 'default'):
            raise ValueError('board pinned')
        conn = sqlite3.connect(live_path)
        try:
            yield kb, conn, None
        finally:
            conn.close()
    monkeypatch.setattr(kt, '_granted_board', granted)
    monkeypatch.setattr(fc, 'replica_path', lambda path: shadow_path)
    monkeypatch.setattr(sync, 'board_slug_for_path', lambda path: 'default')
    return shadow_path


def test_real_replica_read_and_filters(replica):
    result = bt.handle({'action': 'read', 'target': '@task:default@owner/t_123'})
    assert result['ok'], result
    assert result['task']['body'] == 'body'
    assert result['task']['owner'] == 'owner'
    assert result['live'] is False
    assert result['comments'] == []
    assert bt.handle({'action': 'list', 'gateway': 'owner'})['tasks'][0]['id'] == 't_123'
    assert bt.handle({'action': 'list', 'gateway': 'wrong'})['tasks'] == []
    assert not bt.handle({'action': 'read', 'target': 'wrong:t_123'})['ok']
    assert not bt.handle({'action': 'read', 'target': '@task:other/t_123'})['ok']
    assert not bt.handle({'action': 'read', 'target': 't_999'})['ok']


@pytest.mark.parametrize('payload', [
    {'artifact_receipt': [
        {'machine': 'worker-example', 'path': '/work/result.txt', 'sha256': 'a' * 64,
         'attachment_uid': 'att_example', 'warning': None},
        {'machine': None, 'path': 'missing.txt', 'sha256': None,
         'attachment_uid': None, 'warning': 'unavailable'},
    ]},
    {'artifact_receipt': []},
    {},
])
def test_receipt_survives_truncated_replica_history(replica, payload):
    import sqlite3
    with sqlite3.connect(replica) as conn:
        for index, (kind, data) in enumerate([
            ('completed', {'artifact_receipt': [{'path': 'stale'}]}),
            ('completed', payload),
            *[('commented', {}) for _ in range(30)],
        ]):
            conn.execute('INSERT INTO task_events(task_id,kind,payload,created_at) VALUES (?,?,?,?)',
                         ('t_123', kind, json.dumps(data), index + 1))
    result = bt.handle({'action': 'read', 'target': 't_123', 'limit': 2})
    assert result['ok'], result
    assert result['history_truncated']['events'] is True
    assert len(result['events']) == 2
    assert all(event['kind'] == 'commented' for event in result['events'])
    assert result['artifact_receipt'] == payload.get('artifact_receipt', [])


@pytest.mark.parametrize('bad', [{'path': '/work/result.txt'}, 'result.txt', 7])
def test_non_list_completion_receipt_projects_empty(replica, bad):
    import sqlite3
    with sqlite3.connect(replica) as conn:
        conn.execute('INSERT INTO task_events(task_id,kind,payload,created_at) VALUES (?,?,?,?)',
                     ('t_123', 'completed', json.dumps({'artifact_receipt': bad}), 1))
    result = bt.handle({'action': 'read', 'target': 't_123'})
    assert result['ok'], result
    assert result['artifact_receipt'] == []


def test_legacy_replica_without_events_has_empty_receipt(replica):
    result = bt.handle({'action': 'read', 'target': 't_123'})
    assert result['ok'], result
    assert result['artifact_receipt'] == []


def test_comment_refused_without_board_open(replica, monkeypatch):
    from tools import kanban_tools as kt
    monkeypatch.setattr(kt, '_granted_board', lambda *a: pytest.fail('write opened board'))
    assert bt.handle({'action': 'comment', 'target': 't_123', 'message': 'x'})['rejection'] == 'read_only_transport'


def test_list_retains_orchestrator_gate(replica, monkeypatch):
    from tools import kanban_tools as kt
    def refuse(name):
        assert name == 'kanban_list'
        raise ValueError('orchestrator only')
    monkeypatch.setattr(kt, '_require_orchestrator_tool', refuse)
    assert bt.handle({'action': 'list'})['error'] == 'orchestrator only'


def test_missing_replica_no_live_fallback(replica, monkeypatch):
    from hermes_cli import kanban_foreign_card as fc
    monkeypatch.setattr(fc, 'replica_path', lambda path: None)
    assert not bt.handle({'action': 'read', 'target': 't_123'})['ok']
