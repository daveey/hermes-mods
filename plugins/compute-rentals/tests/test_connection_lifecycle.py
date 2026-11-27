"""Registry connections must be closed deterministically, not left to cyclic GC.

``with sqlite3_connection:`` only commits/rolls back. Before the fix every owners
read, binding write and inventory-cache write left a registry.db descriptor open
until the garbage collector ran. These tests disable cyclic GC and hold strong
references to every connection, so a leak shows up as an open connection (and, on
Linux, as a growing /proc/self/fd count).
"""
import gc
import os
import sqlite3
import threading

import pytest

from test_ownership import load
from test_inventory import machine, result

KEY = '["vast","acct","zone","42"]'
IDENTITY = dict(gateway_id='g1', profile='default', session_id='s1')


@pytest.fixture
def opened(monkeypatch):
    """Record every sqlite3 connection opened; hold them so GC can't hide a leak."""
    real = sqlite3.connect
    conns = []

    def recording(*args, **kwargs):
        conn = real(*args, **kwargs)
        conns.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, 'connect', recording)
    gc.disable()
    try:
        yield conns
    finally:
        gc.enable()


def is_closed(conn):
    try:
        conn.execute('SELECT 1')
    except sqlite3.ProgrammingError:
        return True
    return False


def assert_all_closed(conns, minimum=1):
    assert len(conns) >= minimum
    open_ones = [c for c in conns if not is_closed(c)]
    assert open_ones == []


def registry_fds(path):
    fd_dir = '/proc/self/fd'
    if not os.path.isdir(fd_dir):
        return None
    target = os.path.realpath(path)
    count = 0
    for name in os.listdir(fd_dir):
        try:
            if os.path.realpath(os.path.join(fd_dir, name)) == target:
                count += 1
        except OSError:
            pass
    return count


def test_store_init_and_repeated_owner_reads_close_connections(tmp_path, opened):
    store = load('ownership').OwnershipStore(tmp_path / 'registry.db')
    store.attach(KEY, **IDENTITY)
    del opened[:]
    for _ in range(200):
        assert store.owners(KEY) == [IDENTITY]
    assert len(opened) == 200
    assert_all_closed(opened, 200)
    fds = registry_fds(store.path)
    if fds is not None:
        assert fds == 0


def test_store_init_attach_and_detach_close_connections(tmp_path, opened):
    store = load('ownership').OwnershipStore(tmp_path / 'registry.db')
    store.attach(KEY, **IDENTITY)
    store.attach(KEY, **IDENTITY, source='auto')
    store.detach(KEY, **IDENTITY)
    assert store.owners(KEY) == []
    assert_all_closed(opened, 5)


def test_successful_write_commits_then_closes(tmp_path, opened):
    store = load('ownership').OwnershipStore(tmp_path / 'registry.db')
    with store.connect() as db:
        db.execute("INSERT INTO bindings VALUES (?,?,?,?,'manual')", (KEY, 'g1', 'default', 's1'))
    assert is_closed(db)
    # Committed: visible to an independent connection.
    other = sqlite3.connect(store.path)
    try:
        assert other.execute('SELECT COUNT(*) FROM bindings').fetchone() == (1,)
    finally:
        other.close()


def test_failed_write_rolls_back_then_closes(tmp_path, opened):
    store = load('ownership').OwnershipStore(tmp_path / 'registry.db')
    with pytest.raises(RuntimeError, match='boom'):
        with store.connect() as db:
            db.execute("INSERT INTO bindings VALUES (?,?,?,?,'manual')", (KEY, 'g1', 'default', 's1'))
            raise RuntimeError('boom')
    assert is_closed(db)
    assert store.owners(KEY) == []  # rolled back, exception propagated unchanged


def test_failing_statement_inside_block_closes_and_propagates(tmp_path, opened):
    store = load('ownership').OwnershipStore(tmp_path / 'registry.db')
    store.attach(KEY, **IDENTITY)
    with pytest.raises(sqlite3.IntegrityError):
        with store.connect() as db:
            db.execute("INSERT INTO bindings VALUES (?,?,?,?,'manual')", (KEY, 'g1', 'default', 's1'))
    assert is_closed(db)
    assert store.owners(KEY) == [IDENTITY]


def test_close_runs_even_if_rollback_fails(tmp_path, opened):
    store = load('ownership').OwnershipStore(tmp_path / 'registry.db')
    db = store.connect()
    db.close()  # rollback on a closed connection raises ProgrammingError
    with pytest.raises(sqlite3.ProgrammingError):
        db.__exit__(None, None, None)
    assert is_closed(db)


def test_locked_database_attempt_does_not_leak(tmp_path, opened):
    store = load('ownership').OwnershipStore(tmp_path / 'registry.db')
    blocker = sqlite3.connect(store.path)
    blocker.execute('BEGIN IMMEDIATE')
    try:
        with pytest.raises(sqlite3.OperationalError):
            with store.connect(timeout=0.05) as db:
                db.execute("INSERT INTO bindings VALUES (?,?,?,?,'manual')", (KEY, 'g1', 'default', 's1'))
    finally:
        blocker.rollback()
        blocker.close()
    assert is_closed(db)


def test_inventory_init_reads_and_refresh_persistence_close_connections(tmp_path, opened):
    module = load('inventory')
    service = module.InventoryService(tmp_path, providers=('vast',))
    service.register_adapter('vast', lambda: result([machine(provider_id=str(i)) for i in range(5)]))
    del opened[:]
    for _ in range(20):
        data = service.inventory(refresh=True)
    assert len(data['machines']) == 5
    assert data['providers'][0]['persistence_error'] is None
    # Per poll: one ownership query per machine (cache writes are throttled).
    assert len(opened) >= 20 * 5
    assert_all_closed(opened, 20 * 5)
    fds = registry_fds(service.ownership.path)
    if fds is not None:
        assert fds == 0
    # A restart reads the persisted cache through the same close-on-exit path.
    del opened[:]
    module.InventoryService(tmp_path, providers=('vast',))
    assert_all_closed(opened)


def test_inventory_failed_cache_write_does_not_leak(tmp_path, opened):
    service = load('inventory').InventoryService(tmp_path, providers=('vast',))
    entered = threading.Event()
    service.register_adapter('vast', lambda: (entered.set(), result([machine()]))[1])
    del opened[:]
    blocker = sqlite3.connect(service.ownership.path)
    blocker.execute('BEGIN IMMEDIATE')
    try:
        data = service.inventory(refresh=True)
        assert data['providers'][0]['persistence_error'] == 'Inventory cache write failed'
    finally:
        blocker.rollback()
        blocker.close()
    assert_all_closed([c for c in opened if c is not blocker], 2)
