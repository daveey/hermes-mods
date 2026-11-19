"""Observable contracts for mirror accounting, schema writes and read cleanup."""

import sqlite3
import threading
from contextlib import ExitStack, closing, contextmanager

import pytest

from test_kanban_mirror import OWNER, core
from test_kanban_mirror_keyed import keyed, settle  # noqa: F401
from test_withdrawal import ROOT, load_module


def retain_connections(monkeypatch, request):
    """Spy sqlite3.connect, strongly retaining this test thread's connections only.

    The patch is process-global, so a background thread left by an earlier test can
    connect while it is live. A sqlite3 handle may only be used on its creating
    thread, so closing such a handle from the finalizer raises ProgrammingError and
    is not ours to close. Only handles created on the owning thread are retained and
    closed here (deterministically, on that thread); foreign ones pass through.
    """
    owner = threading.get_ident()
    connections = []
    request.addfinalizer(lambda: [conn.close() for conn in connections])
    real_connect = sqlite3.connect

    def retained_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        if threading.get_ident() == owner:
            connections.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, 'connect', retained_connect)
    return connections, retained_connect


def test_connection_spy_leaves_foreign_thread_connections_alone(tmp_path, monkeypatch, request):
    # Regression: a connect() on another thread during the spy's lifetime was retained
    # and then closed by the finalizer on the wrong thread (sqlite3.ProgrammingError).
    # The creator thread stays alive until the finalizer has run, then verifies and
    # closes its own handle on the thread that created it. A new thread is never relied
    # on for that: the OS may or may not reuse a finished thread's ident (CPython 3.11
    # on the CI runner does not), and sqlite3 rejects any other thread's use.
    finalizers = []
    monkeypatch.setattr(request, 'addfinalizer', finalizers.append)
    connections, connect = retain_connections(monkeypatch, request)
    assert len(finalizers) == 1
    created = threading.Event()
    finalized = threading.Event()
    outcome = []
    errors = []

    def creator():
        conn = None
        try:
            conn = connect(tmp_path / 'foreign.sqlite3')
            created.set()
            assert finalized.wait(timeout=10), 'finalizer did not run'
            conn.execute('SELECT 1')  # still open: the spy did not close it
            outcome.append('ok')
        except BaseException as exc:  # reported to the test thread below
            errors.append(exc)
        finally:
            created.set()  # never leave the test thread waiting on a failed creator
            if conn is not None:
                conn.close()

    worker = threading.Thread(target=creator, name='foreign-creator', daemon=True)
    worker.start()
    try:
        assert created.wait(timeout=10), 'creator thread never connected'
        own = connect(tmp_path / 'own.sqlite3')
        finalizers[0]()  # must close ours and must not touch the foreign-thread handle
        assert connections == [own]
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            own.execute('SELECT 1')
    finally:
        finalized.set()
        worker.join(timeout=10)
    assert not worker.is_alive()
    assert errors == []
    assert outcome == ['ok']


def test_skip_is_reported_to_real_core_accounting(keyed):
    # No fake Pass: without the production skip marker the queue still looks right,
    # but the process's real telemetry loses every cheap poll.
    accounting = pytest.importorskip('hermes_cli.loop_accounting')
    k = keyed
    assert isinstance(k.mod['_accounted_pass']('test.probe'), accounting.Pass)
    settle(k)
    name = 'user_steps.mirror_reconcile'
    before = accounting.counters()[name]
    full_before = len(k.passes)
    for _ in range(3):
        k.mirror.reconcile(OWNER)
    after = accounting.counters()[name]
    assert len(k.passes) == full_before
    assert after['runs'] - before['runs'] == 3
    assert after['skips'] - before['skips'] == 3
    for field in ('changes', 'fallbacks', 'rows', 'errors'):
        assert after[field] == before[field], field


def test_schema_setup_is_one_real_write_transaction_per_store_inode(tmp_path, monkeypatch, request):
    mod = load_module('mirror_schema_accounting', ROOT / 'kanban_mirror.py')
    # Keep strong references so constructor handles cannot be cleaned up by GC
    # at a lucky time. A sqlite connection context commits; it does not close.
    connections, retained_connect = retain_connections(monkeypatch, request)

    def new_store(path):
        # Close constructor-owned handles before moving either database. Moving
        # just the main file with live WAL/SHM is not an isolated inode test.
        with ExitStack() as stack, monkeypatch.context() as patch:
            def connect(*args, **kwargs):
                return stack.enter_context(closing(retained_connect(*args, **kwargs)))
            patch.setattr(sqlite3, 'connect', connect)
            return core.ActionStore(path)

    store = new_store(tmp_path / 'actions.sqlite3')
    statements = []
    transactions = []
    real_tx = store._tx

    @contextmanager
    def traced_tx():
        transactions.append('begin')
        with real_tx() as conn:
            conn.set_trace_callback(statements.append)
            try:
                yield conn
            finally:
                conn.set_trace_callback(None)

    monkeypatch.setattr(store, '_tx', traced_tx)
    for _ in range(4):
        mod.Mirror(core, store, boards=[])
    assert transactions == ['begin']
    assert sum('CREATE TABLE IF NOT EXISTS kanban_projections' in s for s in statements) == 1
    with store._read() as conn:
        assert conn.execute('SELECT count(*) FROM kanban_projections').fetchone()[0] == 0

    # A new file at the same path must still initialize; a path-only cache is wrong.
    replacement = new_store(tmp_path / 'replacement.sqlite3')
    old_inode = store.path.stat().st_ino
    replacement_inode = replacement.path.stat().st_ino
    assert replacement_inode != old_inode
    for path in (store.path, replacement.path):
        assert not path.with_name(path.name + '-wal').exists()
        assert not path.with_name(path.name + '-shm').exists()
    replacement.path.replace(store.path)
    assert store.path.stat().st_ino == replacement_inode
    with store._read() as conn:
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name='kanban_projections'"
        ).fetchall() == []
    mod.Mirror(core, store, boards=[])
    assert transactions == ['begin', 'begin']
    assert sum('CREATE TABLE IF NOT EXISTS kanban_projections' in s for s in statements) == 2
    with store._read() as conn:
        assert conn.execute('SELECT count(*) FROM kanban_projections').fetchone()[0] == 0


@pytest.mark.parametrize('exceptional_exit', [False, True])
def test_kept_reader_releases_explicit_read_snapshot(tmp_path, exceptional_exit):
    # Defensive behavior only: BEGIN is outside _kept's documented autocommit
    # caller contract. This kills M5 but is not a reachable production regression.
    mod = load_module('mirror_read_cleanup', ROOT / 'kanban_mirror.py')
    path = tmp_path / 'reader.sqlite3'
    with closing(sqlite3.connect(path)) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('CREATE TABLE values_seen(value INTEGER)')
        writer.execute('INSERT INTO values_seen VALUES (1)')
        writer.commit()
        try:
            try:
                with mod._kept(path) as reader:
                    reader.execute('BEGIN')
                    assert reader.execute('SELECT value FROM values_seen').fetchone()[0] == 1
                    assert reader.in_transaction
                    if exceptional_exit:
                        raise ValueError('caller failed after read')
            except ValueError:
                assert exceptional_exit
            assert not reader.in_transaction
            writer.execute('UPDATE values_seen SET value=2')
            writer.commit()
            with mod._kept(path) as reused:
                assert reused is reader
                assert reused.execute('SELECT value FROM values_seen').fetchone()[0] == 2
        finally:
            mod._evict()


def test_production_mirror_read_exits_have_no_open_transaction(keyed, monkeypatch):
    # Bounded M5 evidence, not general equivalence: on this ordinary reconcile
    # scenario, rollback's guard is false at every observed reader exit.
    k = keyed
    real_kept = k.mod['_kept']
    states = []

    @contextmanager
    def observed_kept(*args, **kwargs):
        with real_kept(*args, **kwargs) as conn:
            try:
                yield conn
            finally:
                states.append(conn.in_transaction)  # before cleanup, not after it

    monkeypatch.setitem(k.mod, '_kept', observed_kept)
    settle(k)
    k.mirror.reconcile(OWNER)
    assert states and not any(states)
    assert len(k.store.requests(OWNER)) == 1
