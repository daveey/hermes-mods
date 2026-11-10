"""Owner lookup: Board binding survives local board-mode off; stale ownership stays fenced."""
from contextlib import contextmanager
import sys
from types import ModuleType, SimpleNamespace
import pytest
from test_relay_retry import plugin

routing = plugin.routing


@pytest.fixture
def owner_env(tmp_path, monkeypatch):
    (tmp_path / 'kanban.db').touch()
    state = {'board_id': 'board'}
    record = {'gateway': 'remote'}
    calls = []
    @contextmanager
    def connect(path):
        yield object()
    def sessions(board, args):
        calls.append((board, args))
        if isinstance(record, Exception):
            raise record
        return {'owners': {'root': record}}
    parent = ModuleType('hermes_cli')
    parent.session_owner = SimpleNamespace(lineage_root=lambda sid, db=None: 'root',
                                          self_gateway=lambda: 'local', owner=lambda root: None)
    parent.kanban_db = SimpleNamespace(kanban_home=lambda: tmp_path)
    parent.kanban_db_sync = SimpleNamespace(sync_enabled=lambda: True, _state=lambda c: state)
    parent.kanban_sync_client = SimpleNamespace(Hub=lambda _: SimpleNamespace(sessions=sessions), load_identity=lambda: {})
    connector = ModuleType('hermes_cli.kanban_db_connect')
    connector.connect_closing = connect
    monkeypatch.setitem(sys.modules, 'hermes_cli', parent)
    monkeypatch.setitem(sys.modules, 'hermes_cli.kanban_db_connect', connector)
    monkeypatch.setattr(routing, '_fallback_cache', {})
    def set_record(value):
        nonlocal record
        record = value
    return parent, state, calls, set_record


def test_board_mode_off_binding_and_cached_owner(owner_env, monkeypatch):
    _, _, calls, set_record = owner_env
    clock = [1.0]
    monkeypatch.setattr(routing.time, 'monotonic', lambda: clock[0])
    assert routing.moved_owner('tip') == 'remote'
    assert calls == [('board', {'action': 'get', 'session_ids': ['root']})]
    assert routing.moved_owner('tip') == 'remote'
    assert len(calls) == 1
    clock[0] = 100
    set_record(OSError('Board offline'))
    assert routing.moved_owner('tip') == 'remote'  # known moved chats never wake locally on a transient outage


def test_no_binding_or_no_known_owner_preserves_core_fail_open(owner_env):
    _, state, calls, set_record = owner_env
    state.clear()
    assert routing.moved_owner('tip') is None
    assert not calls
    state['board_id'] = 'board'
    set_record(OSError('Board offline'))
    assert routing.moved_owner('tip') is None


def test_same_gateway_is_not_moved(owner_env):
    _, _, _, set_record = owner_env
    set_record({'gateway': 'local'})
    assert routing.moved_owner('tip') is None


def test_named_profile_is_resolved_and_passed_by_plugin(tmp_path, monkeypatch):
    import sqlite3
    default = tmp_path / 'state.db'
    named = tmp_path / 'profiles' / 'work' / 'state.db'
    named.parent.mkdir(parents=True)
    for path in (default, named):
        with sqlite3.connect(path) as c:
            c.execute('CREATE TABLE sessions (id TEXT)')
    with sqlite3.connect(named) as c:
        c.execute("INSERT INTO sessions VALUES ('tip')")
    monkeypatch.setattr(plugin, 'state_dbs', lambda: [default, named])
    seen = []
    monkeypatch.setattr(routing, 'moved_owner', lambda sid, db_path=None: seen.append((sid, db_path)) or 'remote')
    monkeypatch.setattr(routing, 'handoff', lambda *a, **kw: seen.append(kw) or 'forwarded')
    assert plugin._moved_owner('tip') == 'remote'
    assert seen == [('tip', named)]
    assert plugin._handoff_ask(None, 'tip', 'remote', 'key', 'prompt', 1000) == 'forwarded'
    assert seen[-1] == {'profile': 'work'}


def _fallback_warnings(caplog):
    return [r for r in caplog.records if 'Board owner fallback unavailable' in r.getMessage()]


def test_unreachable_board_logs_one_line_and_is_cached(owner_env, monkeypatch, caplog):
    """A laptop with no network (DNS failure) is expected: no traceback, no retry storm."""
    import socket
    import urllib.error
    _, _, calls, set_record = owner_env
    clock = [1.0]
    monkeypatch.setattr(routing.time, 'monotonic', lambda: clock[0])
    set_record(urllib.error.URLError(socket.gaierror(8, 'nodename nor servname provided, or not known')))
    caplog.set_level('WARNING', logger='chat_closeout')
    assert routing.moved_owner('tip') is None
    clock[0] = 5.0
    assert routing.moved_owner('tip') is None
    assert len(calls) == 1  # the failure is remembered for the 30 s window
    warnings = _fallback_warnings(caplog)
    assert len(warnings) == 1
    assert warnings[0].exc_info is None  # no traceback: the errors detector must not file it
    assert 'Board unreachable' in warnings[0].getMessage()
    assert 'nodename nor servname' in warnings[0].getMessage()
    clock[0] = 40.0
    assert routing.moved_owner('tip') is None
    assert len(calls) == 2  # retried once the window has passed


def test_unreachable_board_keeps_known_moved_owner(owner_env, monkeypatch, caplog):
    import urllib.error
    _, _, calls, set_record = owner_env
    clock = [1.0]
    monkeypatch.setattr(routing.time, 'monotonic', lambda: clock[0])
    assert routing.moved_owner('tip') == 'remote'
    clock[0] = 100.0
    set_record(urllib.error.URLError(TimeoutError('timed out')))
    assert routing.moved_owner('tip') == 'remote'
    clock[0] = 110.0
    assert routing.moved_owner('tip') == 'remote'  # cached failure still returns the known owner
    assert len(calls) == 2


def test_unexpected_error_keeps_traceback(owner_env, caplog):
    """A real bug in the lookup still logs exc_info so the errors detector files it."""
    _, _, calls, set_record = owner_env
    set_record(KeyError('owners'))
    caplog.set_level('WARNING', logger='chat_closeout')
    assert routing.moved_owner('tip') is None
    warnings = _fallback_warnings(caplog)
    assert len(warnings) == 1
    assert warnings[0].exc_info is not None
