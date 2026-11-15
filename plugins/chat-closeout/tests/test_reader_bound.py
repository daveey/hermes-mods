"""Reader ownership across profile scopes and reload generations (no live databases)."""
import contextlib
import importlib.util
from pathlib import Path
import sqlite3
import sys
import threading
import time
import types

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def readers(tmp_path, monkeypatch, request):
    real_activity = (pytest.importorskip("hermes_cli.kanban_session_activity")
                     if getattr(request, "param", None) == "real" else None)
    opened, hooks, pool = [], [], {}
    real_connect = sqlite3.connect

    class Connection(sqlite3.Connection):
        closed = False

        def close(self):
            self.closed = True
            super().close()

    def connect(*args, **kwargs):
        kwargs.setdefault('factory', Connection)
        conn = real_connect(*args, **kwargs)
        opened.append((str(args[0]), conn))
        return conn

    @contextlib.contextmanager
    def cached_ro(path):
        key = str(path)
        if key not in pool:
            # Fixture-owned files only. Emulate core's exclusive borrow and row factory reset.
            conn = sqlite3.connect(path, check_same_thread=False)
            conn.execute('PRAGMA query_only=ON')
            pool[key] = conn, threading.Lock()
        conn, lock = pool[key]
        with lock:
            conn.row_factory = None
            yield conn

    package = types.ModuleType('hermes_cli')
    package.__path__ = []
    activity = types.ModuleType('hermes_cli.kanban_session_activity')
    activity.cached_ro = cached_ro
    generation = types.ModuleType('hermes_cli.kanban_generation')
    generation.register_reader_cache = hooks.append
    generation.unregister_reader_cache = lambda hook: hooks.remove(hook)
    package.kanban_session_activity = activity
    package.kanban_generation = generation
    for name, mod in [('hermes_cli', package), (activity.__name__, activity), (generation.__name__, generation)]:
        if real_activity is None:
            monkeypatch.setitem(sys.modules, name, mod)
    if real_activity is not None:
        activity = real_activity
    monkeypatch.setattr(sqlite3, 'connect', connect)
    monkeypatch.delenv('CHAT_CLOSEOUT_OFF', raising=False)
    boards = [tmp_path / f'board{i}.db' for i in range(4)]
    shadow = tmp_path / 'kanban' / 'sync-shadow' / 'build.db'
    shadow.parent.mkdir(parents=True)
    for i, path in enumerate(boards + [shadow]):
        with contextlib.closing(sqlite3.connect(path)) as conn, conn:
            conn.execute('CREATE TABLE tasks (id TEXT, title TEXT, status TEXT, created_on TEXT)')
            conn.execute('INSERT INTO tasks VALUES (?, ?, ?, ?)', (f't_{i}', 'card', 'done', 'owner'))
            if path == shadow:
                conn.execute('CREATE TABLE kanban_sync_state (board_id TEXT, pulled_at REAL)')
                conn.execute('INSERT INTO kanban_sync_state VALUES (?, ?)', ('build', time.time()))
    watched = boards + [shadow]
    loaded = []

    def load(scope, generation):
        name = f'bound_scope_{scope}_generation_{generation}'
        spec = importlib.util.spec_from_file_location(name, ROOT / '__init__.py', submodule_search_locations=[str(ROOT)])
        mod = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, mod)
        spec.loader.exec_module(mod)
        loaded.append(mod)
        led = mod.core.Ledger(tmp_path / f'ledger-{scope}-{generation}.db')
        for i in range(5):
            led.write(f'chat{i}', batch=[f't_{i}' if i < 4 else 'owner/default/build/t_4'], open=[])
        monkeypatch.setattr(mod, 'ledger', lambda: led)
        monkeypatch.setattr(mod, 'board_paths', lambda: boards)
        monkeypatch.setattr(mod, '_kanban_root', lambda: tmp_path)
        monkeypatch.setattr(mod, 'remote_cards', lambda: [])
        monkeypatch.setattr(mod, 'session_proof', lambda *a: False)
        return mod

    def is_open(conn):
        try:
            conn.execute('SELECT 1').fetchall()
            return True
        except sqlite3.ProgrammingError:
            return False

    def live():
        return [conn for name, conn in opened if is_open(conn)
                and Path(name.removeprefix('file:').split('?')[0]).resolve() in watched]

    expected = {f'chat{i}': [f't_{i}' if i < 4 else 'owner/default/build/t_4'] for i in range(5)}
    yield types.SimpleNamespace(load=load, live=live, hooks=hooks, pool=pool, activity=activity,
                                expected=expected, boards=boards, shadow=shadow, loaded=loaded)
    for mod in loaded:
        mod.core._structural_reads.clear()
        for name in list(sys.modules):
            if name == mod.__name__ or name.startswith(mod.__name__ + '.'):
                sys.modules.pop(name, None)
    if real_activity is not None:
        from hermes_cli.kanban_generation import evict_readers
        for path in watched:
            evict_readers(str(path.resolve()))
    for _, conn in opened:
        conn.close()


@pytest.mark.parametrize('readers', ['real'], indirect=True)
@pytest.mark.parametrize('journal', ['delete', 'wal'])
def test_real_core_three_scopes_and_reloads(readers, journal):
    for path in readers.boards + [readers.shadow]:
        with contextlib.closing(sqlite3.connect(path)) as conn:
            assert conn.execute(f'PRAGMA journal_mode={journal}').fetchone()[0] == journal
    test_three_scopes_two_reloads_hold_at_most_five_readers(readers)


def test_private_idle_readers_expire_without_another_read(readers, monkeypatch):
    del readers.activity.cached_ro
    mod = readers.load(0, 0)
    now, timers = [0.0], []

    class Timer:
        def __init__(self, delay, callback):
            self.delay, self.callback, self.cancelled = delay, callback, False
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            self.cancelled = True

    monkeypatch.setattr(mod.core.threading, 'Timer', Timer)
    monkeypatch.setattr(mod.core.time, 'monotonic', lambda: now[0])
    assert mod.waiting_source() == readers.expected
    assert timers and timers[0].delay == 300
    now[0] = 300
    entry = mod.core._structural_reads.entries[str(readers.boards[0])]
    with entry[3]:
        timers[0].callback()
        assert len(readers.live()) == 1
        assert timers[-1].delay == 300  # no 100 ms retry loop while borrowed
    now[0] = 600
    timers[-1].callback()
    assert readers.live() == []
    mod._state.pop('waiting_cache', None)
    assert mod.waiting_source() == readers.expected
    assert len(readers.live()) == 5
    mod._unload()
    assert timers[-1].cancelled


def test_unload_at_shared_borrow_does_not_evict_core_reader(readers):
    mod = readers.load(0, 0)
    assert mod.waiting_source() == readers.expected
    original = readers.activity.cached_ro
    @contextlib.contextmanager
    def borrow(path):
        with original(path) as conn:
            mod._unload()
            try:
                yield conn
            except sqlite3.Error:
                # Same exception ownership as core.cached_ro.
                conn.close()
                raise
    readers.activity.cached_ro = borrow
    mod._state.pop('waiting_cache', None)
    assert mod.waiting_source() == {}
    assert all(not c.closed for c, _ in readers.pool.values())


def test_shared_reader_preserves_core_path_spelling(readers, tmp_path, monkeypatch):
    mod = readers.load(0, 0)
    alias = tmp_path / 'alias'
    alias.symlink_to(tmp_path, target_is_directory=True)
    path = alias / readers.boards[0].name
    with readers.activity.cached_ro(path):
        pass
    monkeypatch.setattr(mod, 'board_paths', lambda: [path] + readers.boards[1:])
    assert mod.waiting_source() == readers.expected
    assert len(readers.live()) == 5


def test_shared_reopen_same_inode_and_revision_does_not_reuse_old_result(readers):
    mod = readers.load(0, 0)
    assert mod.waiting_source() == readers.expected
    old, _ = readers.pool.pop(str(readers.boards[0]))
    revision = old.execute('PRAGMA data_version').fetchone()[0]
    old.close()
    with contextlib.closing(sqlite3.connect(readers.boards[0])) as writer, writer:
        writer.execute("UPDATE tasks SET status='running'")
    mod._state.pop('waiting_cache', None)
    readers.expected.pop('chat0')
    assert mod.waiting_source() == readers.expected
    conn, _ = readers.pool[str(readers.boards[0])]
    assert conn.execute('PRAGMA data_version').fetchone()[0] == revision
    assert conn.row_factory is None
    assert not conn.in_transaction


def test_unload_during_private_open_cannot_publish_new_observer(readers, monkeypatch):
    del readers.activity.cached_ro
    mod = readers.load(0, 0)
    original = mod.core.ro_connect
    def connect(*args, **kwargs):
        conn = original(*args, **kwargs)
        mod._unload()
        return conn
    monkeypatch.setattr(mod.core, 'ro_connect', connect)
    assert mod.waiting_source() == {}
    assert not readers.live()
    assert not readers.hooks
    assert not mod.core._structural_reads.entries


def test_unload_older_core_without_unregister(readers):
    del readers.activity.cached_ro
    del sys.modules['hermes_cli.kanban_generation'].unregister_reader_cache
    mod = readers.load(0, 0)
    assert mod.waiting_source() == readers.expected
    mod._unload()
    assert not readers.live()
    # Old core retains the callback; it must retain no live observers.
    assert len(readers.hooks) == 1
    readers.hooks[0](str(readers.boards[0]))


def test_three_scopes_two_reloads_hold_at_most_five_readers(readers):
    for generation in range(3):
        for scope in range(3):
            mod = readers.load(scope, generation)
            assert mod.waiting_source() == readers.expected
    # Keep old generations alive intentionally, as core's callbacks and in-flight calls can.
    assert len(readers.live()) == 5
    for mod in readers.loaded:
        mod._unload()
        assert not mod.core._structural_reads.entries
    assert not readers.hooks
    assert len(readers.live()) == 5  # only core-owned connections remain


@pytest.mark.parametrize('shared', [False, True])
def test_unload_releases_private_readers_and_registry_hook(readers, shared):
    if not shared:
        del readers.activity.cached_ro
    mod = readers.load(0, 0)
    assert mod.waiting_source() == readers.expected
    plugin_connections = [c for c in readers.live() if all(c is not v[0] for v in readers.pool.values())]
    mod._unload()
    assert all(c.closed for c in plugin_connections)
    assert not readers.hooks
    # Borrowed core connections are not ours to close on another plugin's behalf.
    assert all(not c.closed for c, _ in readers.pool.values())


@pytest.mark.parametrize('shared', [False, True])
def test_waiting_source_tracks_commit_on_board_and_shadow(readers, shared):
    if not shared:
        del readers.activity.cached_ro
    mod = readers.load(0, 0)
    assert mod.waiting_source() == readers.expected
    for path, task, chat in [(readers.boards[0], 't_0', 'chat0'), (readers.shadow, 't_4', 'chat4')]:
        with contextlib.closing(sqlite3.connect(path)) as writer, writer:
            writer.execute('UPDATE tasks SET status=? WHERE id=?', ('running', task))
        mod._state.pop('waiting_cache', None)
        readers.expected.pop(chat)
        assert mod.waiting_source() == readers.expected


# ---- lifecycle fences and bounds, each driven at the interleaving it guards -------------------------------------
def _counted():
    calls = []

    def query(conn):
        calls.append(1)
        return [tuple(row) for row in conn.execute('SELECT id, status FROM tasks')]
    return calls, query


def _fake_timers(mod, monkeypatch):
    now, timers = [0.0], []

    class Timer:
        def __init__(self, delay, callback):
            self.delay, self.callback, self.cancelled = delay, callback, False
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            self.cancelled = True

    monkeypatch.setattr(mod.core.threading, 'Timer', Timer)
    monkeypatch.setattr(mod.core.time, 'monotonic', lambda: now[0])
    return now, timers


def test_private_use_extends_the_idle_deadline(readers, monkeypatch):
    del readers.activity.cached_ro
    mod = readers.load(0, 0)
    now, timers = _fake_timers(mod, monkeypatch)
    cache, path = mod.core._structural_reads, str(readers.boards[0])
    calls, query = _counted()
    cache.read(path, 'k', query)
    now[0] = 299
    cache.read(path, 'k', query)  # used one second before the deadline of the open
    now[0] = 300
    timers[0].callback()
    assert path in cache.entries and len(readers.live()) == 1  # idle counts from the last use, not the open
    assert timers[-1].delay == 299
    now[0] = 599
    timers[-1].callback()
    assert not cache.entries and readers.live() == []
    assert len(calls) == 1


def test_cancelled_idle_timer_cannot_displace_its_successor(readers, monkeypatch):
    del readers.activity.cached_ro
    mod = readers.load(0, 0)
    now, timers = _fake_timers(mod, monkeypatch)
    cache, path = mod.core._structural_reads, str(readers.boards[0])
    _, query = _counted()
    cache.read(path, 'k', query)
    cache.clear()  # cancels the first timer, which may already be waiting on the pool lock
    assert timers[0].cancelled and readers.live() == []
    now[0] = 1
    cache.read(path, 'k', query)
    assert len(timers) == 2 and cache._timer is timers[1]
    now[0] = 2
    timers[0].callback()  # the cancelled timer finally runs: it owns nothing
    assert cache._timer is timers[1] and len(timers) == 2  # one deadline timer, still the tracked one
    cache.clear()
    assert timers[1].cancelled and readers.live() == []


def test_unload_between_closed_checks_cannot_reregister_the_hook(readers, monkeypatch):
    del readers.activity.cached_ro
    mod = readers.load(0, 0)
    cache, path = mod.core._structural_reads, str(readers.boards[0])
    real_close, fired = cache._close, []

    def close(*entries):
        if not fired:
            fired.append(1)
            mod._unload()  # lands after _current's first closed check, before it registers the hook
        return real_close(*entries)
    monkeypatch.setattr(cache, '_close', close)
    calls, query = _counted()
    with pytest.raises((sqlite3.Error, OSError)):
        cache.read(path, 'k', query, board=True)
    assert fired and not calls
    assert not readers.hooks and mod.core._GENERATION_HOOK is None  # nothing pins the retired generation
    assert not cache.entries and readers.live() == []


def test_unload_during_private_open_is_refused_by_the_publication_fence_itself(readers, monkeypatch):
    """``read`` also drops the path when ``_current`` raises; the fence must not depend on that cleanup."""
    del readers.activity.cached_ro
    mod = readers.load(0, 0)
    cache, path = mod.core._structural_reads, str(readers.boards[0])
    original = mod.core.ro_connect

    def connect(*args, **kwargs):
        conn = original(*args, **kwargs)
        mod._unload()
        return conn
    monkeypatch.setattr(mod.core, 'ro_connect', connect)
    with pytest.raises(sqlite3.OperationalError):
        cache._current(path, False)
    assert not cache.entries and readers.live() == []


def test_unload_inside_shared_borrow_runs_no_query_and_keeps_no_memo(readers, monkeypatch):
    mod = readers.load(0, 0)
    cache, path = mod.core._structural_reads, str(readers.boards[0])
    original = readers.activity.cached_ro

    @contextlib.contextmanager
    def borrow(target):
        with original(target) as conn:
            mod._unload()  # after read()'s entry check, before the borrowed body
            yield conn
    monkeypatch.setattr(readers.activity, 'cached_ro', borrow)
    calls, query = _counted()
    with pytest.raises(OSError):
        cache.read(path, 'k', query)
    assert not calls and not cache._shared
    assert readers.pool and all(not conn.closed for conn, _ in readers.pool.values())  # core's reader survives


@pytest.mark.parametrize('shared', [False, True])
def test_unloaded_generation_neither_borrows_nor_opens(readers, monkeypatch, shared):
    if not shared:
        del readers.activity.cached_ro
    mod = readers.load(0, 0)
    cache, path = mod.core._structural_reads, str(readers.boards[0])
    mod._unload()
    borrows = []
    if shared:
        original = readers.activity.cached_ro

        @contextlib.contextmanager
        def borrow(target):
            borrows.append(target)
            with original(target) as conn:
                yield conn
        monkeypatch.setattr(readers.activity, 'cached_ro', borrow)
    calls, query = _counted()
    with pytest.raises(OSError):  # a lifecycle refusal, never a SQLite failure
        cache.read(path, 'k', query)
    assert not borrows and not calls
    assert not readers.pool and readers.live() == []  # a retired generation opens nothing, core-owned included


def test_failed_borrowed_read_drops_that_paths_memo(readers):
    mod = readers.load(0, 0)
    cache, path = mod.core._structural_reads, str(readers.boards[0])
    calls, query = _counted()
    first = cache.read(path, 'a', query)
    assert cache.read(path, 'a', query) == first and len(calls) == 1

    def broken(conn):
        raise ValueError('undecodable row')
    with pytest.raises(ValueError):
        cache.read(path, 'b', broken)
    assert path not in cache._shared  # no reference to the borrowed connection outlives a failed read
    assert cache.read(path, 'a', query) == first and len(calls) == 2  # nor does any memo read through it
    conn, _ = readers.pool[path]
    assert not conn.closed and conn.row_factory is None and not conn.in_transaction


def test_shared_memo_is_bounded_by_database_count(readers, monkeypatch):
    mod = readers.load(0, 0)
    cache = mod.core._structural_reads
    monkeypatch.setattr(cache, 'MAX_DATABASES', 2)
    paths = [str(path) for path in readers.boards[:3]]
    counted = {path: _counted() for path in paths}
    for path in paths:
        cache.read(path, 'k', counted[path][1])
    assert list(cache._shared) == paths[1:]  # the least recently read database left the memo
    cache.read(paths[2], 'k', counted[paths[2]][1])
    assert len(counted[paths[2]][0]) == 1  # a retained database still answers from its memo
    cache.read(paths[0], 'k', counted[paths[0]][1])
    assert len(counted[paths[0]][0]) == 2 and len(cache._shared) == 2
    assert len(readers.live()) == 3  # core owns the connections; the bound is on what the plugin retains


def test_borrowed_memo_is_fenced_by_wal_index_generation(readers, monkeypatch):
    mod = readers.load(0, 0)
    cache, path = mod.core._structural_reads, str(readers.boards[0])
    shm = [(1, 1)]
    monkeypatch.setattr(mod.core, '_shm_identity', lambda target: shm[0])
    calls, query = _counted()
    first = cache.read(path, 'k', query)
    assert cache.read(path, 'k', query) == first and len(calls) == 1
    conn = readers.pool[path][0]
    shm[0] = (1, 2)  # the same connection and data_version over another -shm inode
    assert cache.read(path, 'k', query) == first and len(calls) == 2
    assert readers.pool[path][0] is conn
    assert cache.read(path, 'k', query) == first and len(calls) == 2


def test_reopened_core_reader_is_a_new_memo_generation(readers):
    mod = readers.load(0, 0)
    cache, path = mod.core._structural_reads, str(readers.boards[0])
    calls, query = _counted()
    assert cache.read(path, 'k', query) == [('t_0', 'done')]
    old, _ = readers.pool.pop(path)
    revision = old.execute('PRAGMA data_version').fetchone()[0]
    old.close()  # core retired its reader (recovery, eviction); the next borrow opens another
    with contextlib.closing(sqlite3.connect(path)) as writer, writer:
        writer.execute("UPDATE tasks SET status='running'")
    assert cache.read(path, 'k', query) == [('t_0', 'running')] and len(calls) == 2
    conn = readers.pool[path][0]
    # The case the connection fence exists for: another connection reporting the memo's revision number.
    assert conn is not old and conn.execute('PRAGMA data_version').fetchone()[0] == revision


def test_shared_borrow_failure_is_not_kept(readers, monkeypatch):
    mod = readers.load(0, 0)
    board = readers.boards[0]

    def set_status(status):
        with contextlib.closing(sqlite3.connect(board)) as writer, writer:
            writer.execute('UPDATE tasks SET status=?', (status,))
        mod._state.pop('waiting_cache', None)

    set_status('running')
    settled = dict(readers.expected)
    unsettled = {chat: batch for chat, batch in settled.items() if chat != 'chat0'}
    assert mod.waiting_source() == unsettled
    original, failing, refused = readers.activity.cached_ro, [True], []

    @contextlib.contextmanager
    def flaky(target):
        if failing[0] and Path(target) == board:
            refused.append(target)
            raise sqlite3.OperationalError('database is locked')
        with original(target) as conn:
            yield conn
    monkeypatch.setattr(readers.activity, 'cached_ro', flaky)
    set_status('done')
    assert 'chat0' not in mod.waiting_source() and refused  # an unreadable board proves nothing settled
    failing[0] = False
    mod._state.pop('waiting_cache', None)
    assert mod.waiting_source() == settled  # the failed pass left no answer behind
