"""Explicit-home panel readers: isolation, fail-closed canonicalization, legacy control.

Dormant Window2 H slice. Does not claim P/B/R parity, activation, or roster invalidation.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def api(monkeypatch, tmp_path):
    ambient = tmp_path / 'ambient-home'
    ambient.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(ambient))
    monkeypatch.setenv('HOME', str(tmp_path / 'process-home'))
    monkeypatch.delenv('HERMES_KANBAN_HOME', raising=False)
    monkeypatch.delenv('HERMES_KANBAN_DB', raising=False)
    monkeypatch.delenv('HERMES_HUB_STATE', raising=False)
    return load_module(ROOT / 'dashboard' / 'plugin_api.py', '_explicit_home_plugin_api')


def home_mod():
    return load_module(ROOT / 'dashboard' / 'home_context.py', '_explicit_home_context')


def outcome_marker(readers, session: str) -> str:
    conn = readers.conn(readers.state_db())
    assert conn is not None
    row = conn.execute('SELECT outcome_json FROM sessions WHERE id=?', (session,)).fetchone()
    assert row is not None
    payload = json.loads(row['outcome_json'] or '{}')
    return payload['marker']


def seed_home(home: Path, *, session: str, marker: str):
    home.mkdir(parents=True, exist_ok=True)
    (home / 'install_id').write_text('a' * 32)
    (home / 'config.yaml').write_text(json.dumps({'kanban': {'sync': {'gateway': 'gw-explicit'}}}))
    with sqlite3.connect(home / 'state.db') as db:
        db.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY,parent_session_id TEXT,outcome_json TEXT)')
        db.execute(
            'INSERT INTO sessions VALUES (?,?,?)',
            (session, None, json.dumps({'marker': marker, 'kind': 'outcome'})),
        )
    kanban_db = pytest.importorskip('hermes_cli.kanban_db')
    from hermes_cli.kanban_db_connect import connect
    conn = connect(home / 'kanban.db')
    try:
        tid = kanban_db.create_task(conn, title=marker, session_id=session, initial_status='running')
    finally:
        conn.close()
    ledger = home / 'kanban' / 'origin-ledger.db'
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(ledger) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS origin_obligations (
            binding_id TEXT PRIMARY KEY, binding_hash TEXT NOT NULL,
            binding_json TEXT NOT NULL, resolved_session TEXT NOT NULL,
            task_json TEXT NOT NULL, observation_version INTEGER NOT NULL,
            released INTEGER NOT NULL DEFAULT 0)""")
        db.execute("""CREATE TABLE IF NOT EXISTS origin_receipts (
            request_id TEXT PRIMARY KEY NOT NULL, binding_hash TEXT NOT NULL,
            receipt_json TEXT NOT NULL)""")
    (home / 'origin-work.db').write_bytes(marker.encode())
    (home / 'user-steps').mkdir(exist_ok=True)
    return tid


def ctx_for(home: Path, *, life='life-1', auth='auth-1'):
    return home_mod().PanelHomeContext.from_authorized_home(
        home, lifecycle_generation=life, authorization_generation=auth,
    )


def test_missing_home_canonicalization_fails_closed(tmp_path):
    missing = tmp_path / 'no-such-home'
    with pytest.raises(ValueError, match='canonical'):
        ctx_for(missing)


def test_nondirectory_home_canonicalization_fails_closed(tmp_path):
    target = tmp_path / 'not-a-dir'
    target.write_text('file', encoding='utf-8')
    with pytest.raises(ValueError, match='canonical'):
        ctx_for(target)


def test_broken_symlink_canonicalization_fails_closed(tmp_path):
    link = tmp_path / 'broken'
    link.symlink_to(tmp_path / 'missing-target')
    with pytest.raises(ValueError, match='canonical'):
        ctx_for(link)


def test_empty_generation_strings_fail_closed(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    with pytest.raises(ValueError, match='generation'):
        ctx_for(home, life='')
    with pytest.raises(ValueError, match='generation'):
        ctx_for(home, auth='')
    with pytest.raises(ValueError, match='generation'):
        home_mod().PanelHomeContext.from_authorized_home(
            home, lifecycle_generation=1, authorization_generation='auth-1',
        )


def test_state_db_cannot_combine_with_home_context(api, tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    ctx = ctx_for(home)
    with pytest.raises(TypeError, match='state_db'):
        api.CoreReaders(home / 'state.db', home_context=ctx)


def test_context_is_immutable_across_calls(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    ctx = ctx_for(home)
    with pytest.raises(Exception):
        ctx.lifecycle_generation = 'mutated'
    with pytest.raises(Exception):
        ctx.home = home
    assert ctx.lifecycle_generation == 'life-1'
    assert ctx.home == home.resolve(strict=True)


def test_symlink_aliases_canonicalize_to_same_home(tmp_path):
    home = tmp_path / 'real-home'
    home.mkdir()
    alias = tmp_path / 'alias-home'
    alias.symlink_to(home)
    a = ctx_for(home, life='L', auth='A')
    b = ctx_for(alias, life='L', auth='A')
    assert a.home == b.home
    assert a.identity == b.identity


def test_core_readers_home_context_is_keyword_only(api, tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    ctx = ctx_for(home)
    with pytest.raises(TypeError):
        api.CoreReaders(None, ctx)


def test_omitted_context_legacy_reads_ambient_complete_data(api, tmp_path, monkeypatch):
    ambient = Path(os.environ['HERMES_HOME'])
    other = tmp_path / 'other-home'
    seed_home(ambient, session='chat', marker='AMBIENT-MARKER')
    seed_home(other, session='chat', marker='OTHER-MARKER')
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(ambient))
    before = dict(os.environ)
    readers = api.CoreReaders()
    after = dict(os.environ)
    assert before == after
    payload = readers.decision_inputs('chat', [])
    assert payload['outcome_json']['marker'] == 'AMBIENT-MARKER'
    assert readers.state_db() == ambient / 'state.db'
    readers.release()


def test_explicit_home_returns_that_homes_complete_data_not_ambient(api, tmp_path, monkeypatch):
    ambient = Path(os.environ['HERMES_HOME'])
    home_a = tmp_path / 'profiles' / 'researcher'
    home_b = tmp_path / 'tenants' / 'researcher'
    seed_home(ambient, session='chat', marker='AMBIENT-LEAK')
    tid_a = seed_home(home_a, session='chat', marker='HOME-A-DATA')
    tid_b = seed_home(home_b, session='chat', marker='HOME-B-DATA')
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(ambient))
    monkeypatch.setenv('HERMES_HUB_STATE', str(ambient / 'hub-state'))
    env_before = dict(os.environ)
    ctx = ctx_for(home_b)
    readers = api.CoreReaders(home_context=ctx)
    try:
        assert dict(os.environ) == env_before
        assert os.environ.get('HERMES_HOME') == str(ambient)
        assert outcome_marker(readers, 'chat') == 'HOME-B-DATA'
        rec = readers.card(tid_b, 'default')
        assert rec is not None
        assert rec['detail']['task']['title'] == 'HOME-B-DATA'
        assert rec['detail']['task']['id'] == tid_b
        leaked = json.dumps(json.loads(json.dumps(api.json_safe(rec))))
        assert 'AMBIENT-LEAK' not in leaked
        assert 'HOME-A-DATA' not in leaked
        assert readers.state_db() == ctx.home / 'state.db'
        watch = [Path(p).resolve() for p in readers.watch_paths()]
        assert ctx.home / 'state.db' in watch
        assert ambient / 'state.db' not in watch
        assert any(p == ctx.home / 'kanban' / 'origin-ledger.db' for p in watch)
        assert all(ambient not in p.parents and p != ambient for p in watch)
        dirs = [Path(p).resolve() for p in readers.watch_directories()]
        assert all(ctx.home == d or ctx.home in d.parents for d in dirs)
        with sqlite3.connect(readers.state_db()) as db:
            row = db.execute('SELECT outcome_json FROM sessions WHERE id=?', ('chat',)).fetchone()
        assert json.loads(row[0])['marker'] == 'HOME-B-DATA'
    finally:
        readers.release()
    assert dict(os.environ) == env_before
    assert tid_a != tid_b


def test_equal_labels_in_distinct_homes_do_not_collide(api, tmp_path, monkeypatch):
    ambient = Path(os.environ['HERMES_HOME'])
    home_a = tmp_path / 'one' / 'researcher'
    home_b = tmp_path / 'two' / 'researcher'
    seed_home(home_a, session='chat', marker='LABEL-A')
    seed_home(home_b, session='chat', marker='LABEL-B')
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(ambient))
    ra = api.CoreReaders(home_context=ctx_for(home_a))
    rb = api.CoreReaders(home_context=ctx_for(home_b))
    try:
        assert outcome_marker(ra, 'chat') == 'LABEL-A'
        assert outcome_marker(rb, 'chat') == 'LABEL-B'
        assert ra.state_db() != rb.state_db()
        assert ra.snapshot_path() != rb.snapshot_path()
    finally:
        ra.release()
        rb.release()


def test_distinct_lifecycle_and_auth_generations_isolate_snapshot_identity(api, tmp_path):
    home = tmp_path / 'home'
    seed_home(home, session='chat', marker='SAME-HOME')
    same = ctx_for(home, life='life-1', auth='auth-1')
    other_life = ctx_for(home, life='life-2', auth='auth-1')
    other_auth = ctx_for(home, life='life-1', auth='auth-2')
    r1 = api.CoreReaders(home_context=same)
    r2 = api.CoreReaders(home_context=other_life)
    r3 = api.CoreReaders(home_context=other_auth)
    try:
        assert r1.state_db() == r2.state_db() == r3.state_db()
        paths = {r1.snapshot_path(), r2.snapshot_path(), r3.snapshot_path()}
        assert len(paths) == 3
        assert r1.cache_identity() != r2.cache_identity()
        assert r1.cache_identity() != r3.cache_identity()
    finally:
        r1.release()
        r2.release()
        r3.release()


def test_concurrent_readers_with_different_homes(api, tmp_path, monkeypatch):
    ambient = Path(os.environ['HERMES_HOME'])
    home_a = tmp_path / 'conc-a'
    home_b = tmp_path / 'conc-b'
    seed_home(home_a, session='chat', marker='CONC-A')
    seed_home(home_b, session='chat', marker='CONC-B')
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(ambient))
    results = {}
    errors = []
    start = threading.Barrier(2)

    def worker(name, home):
        try:
            readers = api.CoreReaders(home_context=ctx_for(home))
            start.wait(timeout=5)
            try:
                results[name] = outcome_marker(readers, 'chat')
                ctx = readers.home_context
                ctx.lifecycle_generation  # still readable
            finally:
                readers.release()
        except Exception as exc:
            errors.append(repr(exc))

    threads = [
        threading.Thread(target=worker, args=('a', home_a)),
        threading.Thread(target=worker, args=('b', home_b)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == []
    assert results == {'a': 'CONC-A', 'b': 'CONC-B'}


def test_unsupported_hub_adapter_is_unavailable_not_ambient(api, tmp_path, monkeypatch):
    ambient = Path(os.environ['HERMES_HOME'])
    home = tmp_path / 'home'
    seed_home(home, session='chat', marker='HOME')
    hub = tmp_path / 'process-home' / '.local' / 'state' / 'hermes-hub'
    hub.mkdir(parents=True)
    (hub / 'hub.db').write_bytes(b'ambient-hub')
    monkeypatch.setenv('HERMES_HUB_STATE', str(hub))
    readers = api.CoreReaders(home_context=ctx_for(home))
    try:
        unsupported_name = 'UnsupportedHomeAdapter'
        with pytest.raises(Exception) as raised:
            readers.require_scope('hub_db')
        assert type(raised.value).__name__ == unsupported_name
        assert raised.value.scope == 'hub_db'
        watch = [str(Path(p).resolve()) for p in readers.watch_paths()]
        assert str((hub / 'hub.db').resolve()) not in watch
        assert not any('hermes-hub' in item for item in watch)
        with pytest.raises(Exception) as mail:
            readers.require_scope('mail_config')
        assert type(mail.value).__name__ == unsupported_name
        assert mail.value.scope == 'mail_config'
    finally:
        readers.release()


def test_provider_uses_server_trusted_context_not_browser_or_env(api, tmp_path, monkeypatch):
    ambient = Path(os.environ['HERMES_HOME'])
    home = tmp_path / 'provider-home'
    seed_home(home, session='chat', marker='PROVIDER-HOME')
    seed_home(ambient, session='chat', marker='PROVIDER-AMBIENT')
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(ambient))
    env_before = dict(os.environ)
    provider = load_module(ROOT / 'dashboard' / 'producer_provider.py', '_explicit_home_provider')
    ctx = ctx_for(home)
    produced = provider.open_provider({'home_context': ctx, 'profile': 'browser-label'})
    try:
        assert dict(os.environ) == env_before
        assert produced.readers.state_db() == ctx.home / 'state.db'
        assert outcome_marker(produced.readers, 'chat') == 'PROVIDER-HOME'
        with pytest.raises(Exception) as raised:
            produced.readers.require_scope('hub_db')
        assert type(raised.value).__name__ == 'UnsupportedHomeAdapter'
        assert raised.value.scope == 'hub_db'
    finally:
        produced.close()
    assert dict(os.environ) == env_before


def test_provider_ignores_browser_payload_home_fields(tmp_path, monkeypatch):
    home = tmp_path / 'trusted'
    decoy = tmp_path / 'decoy'
    seed_home(home, session='chat', marker='TRUSTED')
    seed_home(decoy, session='chat', marker='DECOY')
    monkeypatch.setenv('HERMES_HOME', str(decoy))
    monkeypatch.setenv('HOME', str(tmp_path))
    provider = load_module(ROOT / 'dashboard' / 'producer_provider.py', '_explicit_home_provider_decoy')
    ctx = ctx_for(home)
    produced = provider.open_provider({'home_context': ctx})
    try:
        body = {
            'sessions': ['chat'],
            'profile': 'decoy',
            'home': str(decoy),
            '_wire_identity': {'feed': 'fixture:serve', 'profile': 'decoy'},
        }
        env_before = dict(os.environ)
        # The browser-shaped body actually enters the producer. It cannot retarget the bound home.
        wire = produced({'body': body, 'accepted': time.monotonic() - 1})
        text = wire.decode() if isinstance(wire, (bytes, bytearray)) else str(wire)
        assert 'DECOY' not in text and 'TRUSTED' in text
        assert dict(os.environ) == env_before
        assert produced.readers.state_db() == ctx.home / 'state.db'
        assert produced.readers.home_context.home == ctx.home
        assert outcome_marker(produced.readers, 'chat') == 'TRUSTED'
        assert body['home'] == str(decoy)
    finally:
        produced.close()
