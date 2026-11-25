"""Explicit-home acquisition boundary: no ambient resolver may run in explicit mode.

Dormant Window2 H slice. Every ambient home/identity resolver of core is replaced by a tripwire that records the
call and raises. A reader method in explicit mode must either return that home's complete data without tripping
one, or raise the typed ``UnsupportedHomeAdapter`` before any resolver runs. Omitted-context behavior is the control.
This does not claim P/B/R parity, projection transport, roster invalidation or activation.
"""
from __future__ import annotations

import contextlib
import importlib
import json
import os
import sys
import time
import traceback
from pathlib import Path

import pytest

from test_explicit_home_context import ROOT, api, ctx_for, load_module, outcome_marker, seed_home  # noqa: F401

core_only = pytest.importorskip('hermes_cli.kanban_db')


class AmbientAccess(AssertionError):
    """An ambient home/identity resolver ran under explicit mode."""


AMBIENT_RESOLVERS = (
    ('hermes_constants', 'get_hermes_home'),
    ('hermes_constants', 'get_default_hermes_root'),
    ('hermes_cli.kanban_db', 'kanban_home'),
    ('hermes_cli.kanban_db', 'boards_root'),
    ('hermes_cli.kanban_db_sync', 'sync_config'),
    ('hermes_cli.profiles', 'get_active_profile_name'),
    ('hermes_cli.kanban_sync_client', 'load_identity'),
    ('hermes_cli.kanban_sync_client', 'board_paths'),
    ('hermes_cli.kanban_sync_client', 'shadow_path'),
    ('hermes_cli.kanban_session_activity', '_board_db_paths'),
    ('hermes_cli.kanban_session_activity', 'remote_links_and_origin_rows'),
    ('hermes_cli.kanban_session_activity', '_remote_links'),
    ('hermes_cli.kanban_session_activity', '_remote_cards'),
    ('hermes_cli.kanban_session_activity', 'session_tasks'),
    ('hermes_cli.kanban_capability_registry', 'hub_db_path'),
    ('hermes_cli.kanban_origin', 'gateway_name'),
    ('hermes_cli.install_identity', 'get_install_id'),
    ('hermes_cli.kanban_execution_observations', 'local_scope'),
    ('hermes_cli.kanban_execution_observations', 'card_scope'),
    ('hermes_cli.subagent_session_activity', 'annotate_subagent_activity'),
    ('hermes_cli.process_session_activity', 'annotate_background_activity'),
)


class Tripwires(list):
    """Recorded ambient-resolver hits (empty list == none ran). `disarmed()` seeds fixtures; `allow(label)` names a
    retained process-identity read (import-time launch labels) so it is inventoried, never silently ignored."""

    def __init__(self):
        super().__init__()
        self.armed = True
        self.allowed: set[str] = set()
        self.allowed_hits: list[str] = []

    @contextlib.contextmanager
    def disarmed(self):
        self.armed = False
        try:
            yield
        finally:
            self.armed = True

    @contextlib.contextmanager
    def allow(self, label: str):
        self.allowed.add(label)
        try:
            yield
        finally:
            self.allowed.discard(label)


@contextlib.contextmanager
def launch_label_read(tripwires):
    """plugin_api resolves its launch profile label once at import (a process label, not a data home)."""
    with tripwires.allow('hermes_cli.profiles.get_active_profile_name'), \
            tripwires.allow('hermes_constants.get_hermes_home'), \
            tripwires.allow('hermes_constants.get_default_hermes_root'):
        yield


def _caller() -> str:
    for frame in reversed(traceback.extract_stack()[:-2]):
        if 'session-panel' in frame.filename and 'tests/' not in frame.filename:
            return f'{Path(frame.filename).name}:{frame.lineno}'
    return '?'


@pytest.fixture
def tripwires(monkeypatch):
    """Replace every ambient resolver (and every by-name import of it) with a recording tripwire.

    Modules first imported while the tripwires are armed bind the tripwire by name and monkeypatch cannot see them:
    teardown sweeps every module and restores the original, so no later test inherits a tripwire."""
    hits = Tripwires()
    armed: list[tuple[str, str, object, object]] = []
    for module_name, attr in AMBIENT_RESOLVERS:
        try:
            original = getattr(importlib.import_module(module_name), attr)
        except (ImportError, AttributeError):
            continue

        def make(label, original):
            def trip(*args, **kwargs):
                if not hits.armed:
                    return original(*args, **kwargs)
                if label in hits.allowed:
                    hits.allowed_hits.append(label)
                    return original(*args, **kwargs)
                hits.append(f'{label} @ {_caller()}')
                raise AmbientAccess(label)
            return trip
        trip = make(f'{module_name}.{attr}', original)
        armed.append((module_name, attr, original, trip))
        for mod in list(sys.modules.values()):
            if not getattr(mod, '__name__', '').startswith('hermes'):
                continue
            try:
                same = getattr(mod, attr, None) is original
            except Exception:
                same = False
            if same:
                monkeypatch.setattr(mod, attr, trip)
    yield hits
    for _module_name, attr, original, trip in armed:
        for mod in list(sys.modules.values()):
            try:
                bound = getattr(mod, attr, None) is trip
            except Exception:
                bound = False
            if bound:
                setattr(mod, attr, original)


@pytest.fixture
def worlds(api, tmp_path, monkeypatch):
    """An ambient home carrying decoy data and one explicit home carrying the data the reader must return."""
    ambient = Path(os.environ['HERMES_HOME'])
    explicit = tmp_path / 'tenants' / 'researcher'
    seed_home(ambient, session='chat', marker='AMBIENT-MARKER')
    tid = seed_home(explicit, session='chat', marker='EXPLICIT-DATA')
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(ambient))
    monkeypatch.setenv('HERMES_HUB_STATE', str(ambient / 'hub-state'))
    return {'ambient': ambient, 'explicit': explicit, 'task_id': tid}


def blob(value) -> str:
    return json.dumps(api_json(value), sort_keys=True, default=str)


def api_json(value):
    if isinstance(value, dict):
        return {str(k): api_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [api_json(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


SUPPORTED = {
    'lineage': lambda r, w: r.lineage('chat'),
    'panel_stamp': lambda r, w: r.panel_stamp('chat'),
    'panel_stamps': lambda r, w: r.panel_stamps(['chat']),
    'stamps': lambda r, w: r.stamps(),
    'board_stamps': lambda r, w: r.board_stamps(),
    'board_changes': lambda r, w: r.board_changes({}),
    'changed_cards': lambda r, w: r.changed_cards({}),
    'card': lambda r, w: r.card(w['task_id'], 'default'),
    'boards': lambda r, w: r.boards(),
    'watch_paths': lambda r, w: r.watch_paths(),
    'watch_directories': lambda r, w: r.watch_directories(),
    'external_paths': lambda r, w: r.external_paths(),
    'external_stamps': lambda r, w: r.external_stamps(),
    'publication_paths': lambda r, w: sorted(r.publication_paths()),
    'reconciliation_stamp': lambda r, w: r.reconciliation_stamp(),
    'diagnostic_stale': lambda r, w: r.diagnostic_stale({}),
}


@pytest.mark.parametrize('name', sorted(SUPPORTED))
def test_supported_explicit_reader_returns_that_homes_data_without_ambient(name, api, worlds, tripwires):
    ctx = ctx_for(worlds['explicit'])
    readers = api.CoreReaders(home_context=ctx)
    try:
        value = SUPPORTED[name](readers, worlds)
    finally:
        readers.release()
    assert tripwires == []
    text = blob(value)
    assert 'AMBIENT-MARKER' not in text
    ambient = str(worlds['ambient'])
    assert ambient not in text.replace(str(ctx.home), '')


def test_supported_card_and_state_data_are_complete(api, worlds, tripwires):
    readers = api.CoreReaders(home_context=ctx_for(worlds['explicit']))
    try:
        card = readers.card(worlds['task_id'], 'default')
        outcome = json.loads(readers.conn(readers.state_db()).execute(
            'SELECT outcome_json FROM sessions WHERE id=?', ('chat',)).fetchone()['outcome_json'])
        stamps = readers.stamps()
        changed, cursor = readers.changed_cards({})
    finally:
        readers.release()
    assert tripwires == []
    assert card['detail']['task']['title'] == 'EXPLICIT-DATA'
    assert card['detail']['task']['id'] == worlds['task_id']
    assert card['board'] == 'default'
    assert outcome['marker'] == 'EXPLICIT-DATA'
    assert changed == {} and cursor
    assert any(str(Path(p).parent) == str(readers.home_context.home) or str(readers.home_context.home) in str(p)
               for p, _ in stamps)


ACQUIRED = {
    'decision_inputs': lambda r: r.decision_inputs('chat', ['chat']),
    'user_step_requests': lambda r: r.user_step_requests('chat', ['chat']),
    'linked': lambda r: r.linked(['chat']),
    'done': lambda r: r.done(['chat']),
    'session_tasks': lambda r: r._session_tasks(['chat'], ('running', 'ready', 'todo', 'blocked', 'review')),
    'external_capture': lambda r: r.external_capture(
        {'chat': {'lineage': ['chat'], 'bundle': {}, 'refs': []}}),
    'external_capture_inventory': lambda r: r.external_capture(
        {'chat': {'lineage': ['chat'], 'bundle': {}, 'refs': []}}, include_inventory=True),
    'remote_inventory': lambda r: r._remote_inventory(
        {'remote': {}, 'ids': ['chat']}, ['chat'], ('running',)),
    'snapshot_select_external': lambda r: r.snapshot_select_external(
        {'replica': None, 'origin': [], 'history': [], 'gateway': 'gw-explicit', 'profile': 'researcher'},
        ['chat'], observed_at=0),
    'panel_inputs': lambda r: r.panel_inputs('chat', ['chat']),
}


@pytest.mark.parametrize('name', sorted(ACQUIRED))
def test_supported_acquisition_returns_home_data_without_ambient(name, api, worlds, tripwires):
    readers = api.CoreReaders(home_context=ctx_for(worlds['explicit']))
    try:
        result = ACQUIRED[name](readers)
    finally:
        readers.release()
    assert tripwires == [], f'ambient resolver ran: {tripwires}'
    text = blob(result)
    assert 'AMBIENT-MARKER' not in text
    if name in {'linked', 'session_tasks', 'done'}:
        assert worlds['task_id'] in text or 'EXPLICIT-DATA' in text
    if name == 'panel_inputs':
        assert result['outcome_json']['marker'] == 'EXPLICIT-DATA'
        assert result['gateway'] == 'gw-explicit'
    if name == 'decision_inputs':
        assert result['outcome_json']['marker'] == 'EXPLICIT-DATA'


def test_hub_and_mail_remain_typed_unavailable(api, worlds, tripwires):
    readers = api.CoreReaders(home_context=ctx_for(worlds['explicit']))
    try:
        for scope in ('hub_db', 'mail_config'):
            with pytest.raises(Exception) as raised:
                readers.require_scope(scope)
            assert type(raised.value).__name__ == 'UnsupportedHomeAdapter'
            assert raised.value.scope == scope
    finally:
        readers.release()
    assert tripwires == []


def test_missing_install_id_refuses_user_steps_not_empty(api, worlds, tripwires, tmp_path):
    home = tmp_path / 'tenants' / 'bare'
    with tripwires.disarmed():
        seed_home(home, session='chat', marker='BARE')
        (home / 'install_id').unlink()
    readers = api.CoreReaders(home_context=ctx_for(home))
    try:
        with pytest.raises(Exception) as raised:
            readers.decision_inputs('chat', ['chat'])
    finally:
        readers.release()
    assert tripwires == []
    assert type(raised.value).__name__ == 'UnsupportedHomeAdapter'
    assert raised.value.scope == 'user_steps'


def test_replica_links_without_gateway_refuses_inventory(api, worlds, tripwires, tmp_path):
    home = tmp_path / 'tenants' / 'replica-only'
    with tripwires.disarmed():
        seed_home(home, session='chat', marker='REPLICA-CFG')
        (home / 'config.yaml').write_text(json.dumps({'kanban': {'sync': {'links_reader': 'replica'}}}))
    readers = api.CoreReaders(home_context=ctx_for(home))
    try:
        with pytest.raises(Exception) as raised:
            readers.external_capture({'chat': {'lineage': ['chat'], 'bundle': {}, 'refs': []}})
    finally:
        readers.release()
    assert tripwires == []
    assert type(raised.value).__name__ == 'UnsupportedHomeAdapter'
    assert raised.value.scope == 'external_inventory'


def test_done_history_reader_uses_home_ledger_not_ambient(api, worlds, tripwires, monkeypatch):
    readers = api.CoreReaders(home_context=ctx_for(worlds['explicit']))
    monkeypatch.setattr(readers, '_session_tasks', lambda ids, statuses: [])
    try:
        result = readers.done(['chat'])
    finally:
        readers.release()
    assert tripwires == []
    assert 'AMBIENT-MARKER' not in blob(result)


def test_unavailable_hits_are_recorded_for_the_producer(api, worlds):
    readers = api.CoreReaders(home_context=ctx_for(worlds['explicit']))
    try:
        assert readers.unavailable_seen() == frozenset()
        with pytest.raises(Exception):
            readers.require_scope('hub_db')
        assert readers.unavailable_seen() == frozenset({'hub_db'})
    finally:
        readers.release()


def test_omitted_context_control_keeps_ambient_external_acquisition(api, worlds, monkeypatch):
    """Legacy behavior is unchanged: the same reader calls the ambient inventory (so the gate is explicit-only)."""
    from hermes_cli import kanban_db_sync as sync
    from hermes_cli import kanban_session_activity as ksa
    calls = []

    def ambient(**kwargs):
        calls.append('remote_links_and_origin_rows')
        return ([{'id': 'ambient-card', 'session': 'chat', 'title': 'AMBIENT-MARKER'}], [])
    monkeypatch.setattr(ksa, 'remote_links_and_origin_rows', ambient)
    monkeypatch.setattr(sync, 'sync_config', lambda: {})
    readers = api.CoreReaders()
    try:
        result = readers.external_capture({'chat': {'lineage': ['chat'], 'bundle': {}, 'refs': []}})
    finally:
        readers.release()
    assert calls == ['remote_links_and_origin_rows']
    assert 'AMBIENT-MARKER' in blob(result)


def test_omitted_context_control_keeps_ambient_panel_inputs_resolution(api, worlds, monkeypatch):
    seen = []
    readers = api.CoreReaders()
    monkeypatch.setattr(readers.ksc, 'load_identity', lambda: seen.append('load_identity') or (_ for _ in ()).throw(
        RuntimeError('standalone')))
    try:
        out = readers.panel_inputs('chat', ['chat'])
    finally:
        readers.release()
    assert seen == ['load_identity']
    assert out['outcome_json']['marker'] == 'AMBIENT-MARKER'


def payload_for(sessions, **extra):
    body = {'sessions': sessions, 'profile': 'decoy', 'home': '/nonexistent/decoy',
            '_wire_identity': {'feed': 'fixture:serve', 'profile': 'decoy'}, **extra}
    return {'body': body, 'accepted': time.monotonic() - 1}


def populated(produced, make_payload, timeout=60):
    """Lanes own computation: the first answer can precede the bundle, so poll until a chat bundle is present."""
    import json as _json
    deadline = time.monotonic() + timeout
    while True:
        wire = produced(make_payload())
        try:
            has = bool(_json.loads(wire).get('bundles'))
        except ValueError:
            has = True
        if has or time.monotonic() > deadline:
            return wire
        time.sleep(0.05)


def test_provider_produced_payload_is_complete_home_data_and_never_ambient(api, worlds, tripwires):
    """The produced(payload) entry with a browser-shaped body: populated COMPLETE wire, not unavailable."""
    provider = load_module(ROOT / 'dashboard' / 'producer_provider.py', '_explicit_acquisition_provider')
    ctx = ctx_for(worlds['explicit'])
    env_before = dict(os.environ)
    with launch_label_read(tripwires):
        produced = provider.open_provider({'home_context': ctx})  # import-time launch label: retained, see inventory
    try:
        wire = populated(produced, lambda: payload_for(['chat']))
        assert tripwires == []
        assert produced.readers.home_context.home == ctx.home
        text = blob(wire)
        assert 'AMBIENT-MARKER' not in text
        assert str(worlds['ambient']) not in text.replace(str(ctx.home), '')
        assert 'EXPLICIT-DATA' in text or worlds['task_id'] in text
    finally:
        produced.close()
    assert dict(os.environ) == env_before


def test_provider_classification_is_typed_only_for_recorded_gate_refusals(api, worlds):
    provider = load_module(ROOT / 'dashboard' / 'producer_provider.py', '_explicit_acquisition_provider2')
    produced = provider.open_provider({'home_context': ctx_for(worlds['explicit'])})
    refused = 'UnsupportedHomeAdapter: explicit-home adapter unavailable for hub_db'
    try:
        produced.feed.failures = {'a': {'error': refused}}
        assert produced._unavailable_error(['a']) is None  # nothing recorded by a gate: never inferred from text
        with pytest.raises(Exception):
            produced.readers.require_scope('hub_db')
        typed = produced._unavailable_error(['a'])
        assert type(typed).__name__ == 'UnsupportedHomeAdapter' and typed.scope == 'hub_db'
        produced.feed.failures = {'a': {'error': refused}, 'b': {'error': 'RuntimeError: boom'}}
        assert produced._unavailable_error(['a', 'b']) is None  # one generic failure keeps the generic error
        produced.feed.failures = {'b': {'error': 'RuntimeError: boom'}}
        assert produced._unavailable_error(['b']) is None
        produced.feed.failures = {}
        assert produced._unavailable_error(['a']) is None
    finally:
        produced.close()


def test_provider_legacy_context_free_control_still_constructs_ambient_readers(api, worlds):
    provider = load_module(ROOT / 'dashboard' / 'producer_provider.py', '_explicit_acquisition_provider3')
    produced = provider.open_provider({})
    try:
        assert getattr(produced.readers, 'home_context', None) is None
        assert produced.readers.state_db() == worlds['ambient'] / 'state.db'
    finally:
        produced.close()


def test_snapshot_and_timing_default_to_the_explicit_home_namespace(api, worlds, tripwires):
    provider = load_module(ROOT / 'dashboard' / 'producer_provider.py', '_explicit_acquisition_provider4')
    ctx = ctx_for(worlds['explicit'])
    with launch_label_read(tripwires):
        produced = provider.open_provider({'home_context': ctx})  # import-time launch label: retained
    try:
        assert api.save_snapshot(produced.feed) is True
        assert api.write_timing(produced.feed) is True
        expected = produced.readers.snapshot_path()
        assert expected.parent == ctx.home / 'session-panel'
        assert expected.is_file()
        assert produced.readers.timing_path().is_file()
        assert produced.readers.timing_path().parent == ctx.home / 'session-panel'
        assert not (worlds['ambient'] / 'session-panel').exists()
        assert api.load_snapshot(readers=produced.readers) is not None
    finally:
        produced.close()
    assert tripwires == []


def test_child_snapshot_uses_authorized_home_scope_not_exec_argv(api, worlds, monkeypatch):
    provider = load_module(ROOT / 'dashboard' / 'producer_provider.py', '_snapshot_scope_provider')
    child_api = provider.open_provider({'home_context': ctx_for(worlds['explicit'])})
    # open_provider owns a separate API module, just like the real exec import.
    child = child_api.api
    monkeypatch.setattr(child, 'FEED_ROLE', 'producer')
    monkeypatch.setattr(child, 'FEED_ID', 'fixture:producer')
    try:
        assert child.save_snapshot(child_api.feed, claim=True)
        assert child.write_timing(child_api.feed)
        path = child_api.readers.snapshot_path()
        assert 'producer' not in path.name
        head = json.loads(path.read_text().splitlines()[0])
        timing = json.loads(child_api.readers.timing_path().read_text())
        assert head['feed'] != 'fixture:producer' and timing['feed'] == head['feed']
        assert head['profile'] == timing['profile'] == child_api.readers._home_identity()['profile']
        assert child.load_snapshot(readers=child_api.readers) is not None
    finally:
        child_api.close()


def test_snapshot_paths_of_two_homes_with_equal_labels_do_not_collide(api, worlds, tmp_path):
    other = tmp_path / 'other' / 'researcher'
    seed_home(other, session='chat', marker='OTHER')
    a = api.CoreReaders(home_context=ctx_for(worlds['explicit']))
    b = api.CoreReaders(home_context=ctx_for(other))
    try:
        assert a.snapshot_path() != b.snapshot_path()
        assert a.timing_path() != b.timing_path()
        assert a.snapshot_path().parent != b.snapshot_path().parent
    finally:
        a.release()
        b.release()


def test_legacy_snapshot_and_timing_paths_are_unchanged(api, worlds):
    readers = api.CoreReaders()
    try:
        assert readers.snapshot_path() == api._snapshot_path()
        assert readers.timing_path() == api.timing_path()
    finally:
        readers.release()


def test_explicit_state_is_not_shared_between_concurrent_homes_with_tripwires(api, worlds, tripwires, tmp_path):
    other = tmp_path / 'tenants2' / 'researcher'
    with tripwires.disarmed():
        seed_home(other, session='chat', marker='OTHER-DATA')
    a = api.CoreReaders(home_context=ctx_for(worlds['explicit']))
    b = api.CoreReaders(home_context=ctx_for(other))
    try:
        assert outcome_marker(a, 'chat') == 'EXPLICIT-DATA'
        assert outcome_marker(b, 'chat') == 'OTHER-DATA'
        assert a.stamps() != b.stamps()
    finally:
        a.release()
        b.release()
    assert tripwires == []


def seed_replica_only_card(home: Path) -> str:
    from hermes_cli import kanban_db
    from hermes_cli.kanban_db_connect import connect
    shadow = home / 'kanban' / 'sync-shadow' / 'default.db'
    shadow.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(shadow)
    try:
        return kanban_db.create_task(conn, title='REPLICA-ONLY', session_id='chat', initial_status='running')
    finally:
        conn.close()


def test_replica_card_owner_is_home_local_not_ambient(api, worlds, tripwires):
    with tripwires.disarmed():
        replica_id = seed_replica_only_card(worlds['explicit'])
    readers = api.CoreReaders(home_context=ctx_for(worlds['explicit']))
    try:
        rec = readers.card(replica_id, 'default')
    finally:
        readers.release()
    assert tripwires == []
    assert rec['detail']['source'] == 'replica'
    assert rec['detail']['task']['title'] == 'REPLICA-ONLY'
    assert 'owner' in rec['detail']


def test_live_card_is_served_even_when_a_replica_exists_for_other_cards(api, worlds, tripwires):
    with tripwires.disarmed():
        seed_replica_only_card(worlds['explicit'])
    readers = api.CoreReaders(home_context=ctx_for(worlds['explicit']))
    try:
        rec = readers.card(worlds['task_id'], 'default')
    finally:
        readers.release()
    assert tripwires == []
    assert rec['detail']['source'] == 'live'
    assert rec['detail']['task']['title'] == 'EXPLICIT-DATA'


def test_every_gate_scope_is_declared_and_legacy_never_gates(api, worlds):
    from_ctx = api.CoreReaders(home_context=ctx_for(worlds['explicit']))
    legacy = api.CoreReaders()
    try:
        for scope in sorted(api.EXPLICIT_UNAVAILABLE_SCOPES):
            with pytest.raises(Exception) as raised:
                from_ctx.require_scope(scope)
            assert type(raised.value).__name__ == 'UnsupportedHomeAdapter' and raised.value.scope == scope
            legacy.require_scope(scope)  # legacy never raises
        from_ctx.require_scope('not-a-gated-scope')
        assert from_ctx.unavailable_seen() == api.EXPLICIT_UNAVAILABLE_SCOPES
        assert legacy.unavailable_seen() == frozenset()
    finally:
        from_ctx.release()
        legacy.release()


@pytest.mark.parametrize('which', ['capture_local', 'capture_external'])
def test_board_capture_module_does_not_use_ambient_on_direct_call(which, api, worlds, tripwires):
    """The module function is a second entry past the reader methods; it must not resolve process home."""
    capture = api.CoreReaders.snapshot_modules()
    readers = api.CoreReaders(home_context=ctx_for(worlds['explicit']))
    try:
        call = getattr(capture, which)
        with pytest.raises((TypeError, ValueError, AttributeError)):
            call(readers, None, None, None, None) if which == 'capture_local' else call(readers, None, None, None, {})
    finally:
        readers.release()
    assert tripwires == []


def test_panel_producer_returns_populated_complete_wire(api, worlds, tripwires):
    provider = load_module(ROOT / 'dashboard' / 'producer_provider.py', '_explicit_sources_provider')
    ctx = ctx_for(worlds['explicit'])
    env_before = dict(os.environ)
    with launch_label_read(tripwires):
        produced = provider.open_provider({'home_context': ctx})
    try:
        wire = populated(produced, lambda: payload_for(['chat']))
    finally:
        produced.close()
    assert tripwires == []
    assert dict(os.environ) == env_before
    text = blob(wire)
    assert 'EXPLICIT-DATA' in text
    assert worlds['task_id'] in text
    assert 'AMBIENT-MARKER' not in text


def test_two_homes_equal_labels_contradictory_rows(api, worlds, tripwires, tmp_path):
    other = tmp_path / 'tenants2' / 'researcher'
    with tripwires.disarmed():
        other_id = seed_home(other, session='chat', marker='OTHER-DATA')
    a = api.CoreReaders(home_context=ctx_for(worlds['explicit']))
    b = api.CoreReaders(home_context=ctx_for(other))
    try:
        left = a.linked(['chat'])
        right = b.linked(['chat'])
        from test_explicit_home_context import outcome_marker
        assert outcome_marker(a, 'chat') == 'EXPLICIT-DATA'
        assert outcome_marker(b, 'chat') == 'OTHER-DATA'
        assert worlds['task_id'] in blob(left)
        assert other_id in blob(right)
        assert worlds['task_id'] not in blob(right)
        assert other_id not in blob(left)
        assert a._home_identity()['profile'] == b._home_identity()['profile']
        assert a.home_context.home != b.home_context.home
    finally:
        a.release()
        b.release()
    assert tripwires == []


def test_capture_local_returns_home_card(api, worlds, tripwires):
    readers = api.CoreReaders(home_context=ctx_for(worlds['explicit']))
    mod = readers.snapshot_modules()
    m = mod.snapshot
    path = str(worlds['explicit'] / 'kanban.db')
    source = m.SourceKey('gw-explicit', 'researcher', 'default', 'local', path)
    store = m.SnapshotStore()
    store.add_source(source)
    hint = mod.CaptureHints(store.version(source), store.owner_token(source), 1800000000.0)
    try:
        part = readers.capture_local(source, None, store.demands(source), hint)
    finally:
        readers.release()
    assert tripwires == []
    assert 'kanban.db' in part.receipt.source.canonical_path
    assert part.receipt.source.gateway == 'gw-explicit'


def test_stale_missing_ledger_is_not_fresh_empty_success(api, worlds, tripwires, tmp_path):
    home = tmp_path / 'tenants' / 'broken-ledger'
    with tripwires.disarmed():
        seed_home(home, session='chat', marker='BROKEN')
        ledger = home / 'kanban' / 'origin-ledger.db'
        ledger.write_bytes(b'not-a-sqlite-database')
    readers = api.CoreReaders(home_context=ctx_for(home))
    try:
        with pytest.raises(Exception):
            readers.done(['chat'])
    finally:
        readers.release()
    assert tripwires == []
