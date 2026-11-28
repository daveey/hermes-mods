"""A board write wakes the lanes cursor capture. Quiet sibling boards keep their
last revision instead of a fresh connection + read on every capture."""
import importlib.util
import os
from contextlib import contextmanager
from pathlib import Path

import pytest

core_fixtures = pytest.importorskip('tests.hermes_cli.test_kanban_governors')
from hermes_cli import kanban_db as kb, kanban_db_connect as kbc

board = core_fixtures.board
clean = core_fixtures.clean


@pytest.fixture
def api():
    spec = importlib.util.spec_from_file_location('sibling_stamp_api', Path(__file__).parents[1] / 'dashboard/plugin_api.py')
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def _counting(api, monkeypatch):
    merge = api._sibling('board_merge')
    original = merge.read_board
    opened = []

    @contextmanager
    def read(path, **kwargs):
        opened.append(Path(path).parent.name)
        with original(path, **kwargs) as db:
            yield db
    monkeypatch.setattr(merge, 'read_board', read)
    return opened



def _settle(api, monkeypatch):
    # ctime cannot be backdated: move the capture clock instead.
    real = api.time.time_ns
    monkeypatch.setattr(api.time, 'time_ns', lambda: real() + 5_000_000_000)


def _warm(api):
    # The first read-only open of a fresh fixture board creates its empty -wal,
    # a one-time stamp change (production boards always have one).
    api._capture_cursor()
    return api._capture_cursor()[0]


def test_quiet_sibling_is_not_reopened_on_each_capture(board, api, monkeypatch):
    kb.create_board('beta')
    kb.create_board('gamma')
    opened = _counting(api, monkeypatch)
    _settle(api, monkeypatch)
    first = _warm(api)
    assert sorted(set(opened)) == ['beta', 'gamma']
    opened.clear()
    for _ in range(3):
        again, _ = api._capture_cursor()
        assert again == first
    assert opened == [], 'unchanged settled siblings are kept, not reread'


def test_a_sibling_write_after_the_stamp_is_seen(board, api, monkeypatch):
    kb.create_board('beta')
    kb.create_board('gamma')
    opened = _counting(api, monkeypatch)
    _settle(api, monkeypatch)
    before = _warm(api)
    opened.clear()
    with kbc.connect(board='beta') as db:
        kb.create_task(db, title='sibling changed', assignee='default', tenant='other')
    after, _ = api._capture_cursor()
    assert after != before
    assert opened == ['beta'], 'only the written sibling is reread'


def test_a_recently_written_sibling_is_always_reread(board, api, monkeypatch):
    kb.create_board('beta')
    meta = next(b for b in kb.list_boards() if b['slug'] == 'beta')
    opened = _counting(api, monkeypatch)
    _warm(api)
    newest = max(max(s.st_mtime_ns, s.st_ctime_ns) for s in (
        os.stat(meta['db_path'] + suffix) for suffix in ('', '-wal') if os.path.exists(meta['db_path'] + suffix)))
    # Half a second after its last write: a same-tick later write could leave
    # an identical stamp, so the stamp is not trusted yet.
    monkeypatch.setattr(api.time, 'time_ns', lambda: newest + 500_000_000)
    opened.clear()
    api._capture_cursor()
    api._capture_cursor()
    assert opened.count('beta') == 2


def test_a_failed_sibling_read_is_never_kept(board, api, monkeypatch):
    import sqlite3
    kb.create_board('beta')
    _settle(api, monkeypatch)
    merge = api._sibling('board_merge')
    original = merge.read_board
    failing = [True]
    calls = []

    @contextmanager
    def read(path, **kwargs):
        calls.append(path)
        if failing[0] and '/beta/' in str(path):
            raise sqlite3.OperationalError('fixture sibling failure')
        with original(path, **kwargs) as db:
            yield db
    monkeypatch.setattr(merge, 'read_board', read)
    failed, _ = api._capture_cursor()
    assert any(entry[1] == 'unavailable' for entry in failed if isinstance(entry, tuple) and len(entry) == 2)
    failing[0] = False
    recovered, _ = api._capture_cursor()
    assert recovered != failed
    assert not any(entry[1] == 'unavailable' for entry in recovered if isinstance(entry, tuple) and len(entry) == 2)


def test_a_wal_index_header_change_alone_forces_a_reread(board, api, monkeypatch):
    # A WAL commit becomes visible when the writer updates the -shm header,
    # after its last -wal write and without moving any file stat. A header
    # change with db/-wal/-journal untouched must not reuse the kept revision.
    kb.create_board('beta')
    meta = next(b for b in kb.list_boards() if b['slug'] == 'beta')
    shm = meta['db_path'] + '-shm'
    opened = _counting(api, monkeypatch)
    _settle(api, monkeypatch)
    _warm(api)
    assert os.path.exists(shm)
    files = [meta['db_path'] + s for s in ('', '-wal', '-journal') if os.path.exists(meta['db_path'] + s)]
    stats = [(s.st_size, s.st_mtime_ns, s.st_ctime_ns) for s in map(os.stat, files)]
    before = api._settled_stamp(meta['db_path'])
    fd = os.open(shm, os.O_RDWR)
    header = os.pread(fd, 48, 0)
    try:
        os.pwrite(fd, bytes([header[8] ^ 0xFF]), 8)  # iChange byte of aHdr[0]
        assert [(s.st_size, s.st_mtime_ns, s.st_ctime_ns) for s in map(os.stat, files)] == stats
        assert api._settled_stamp(meta['db_path']) != before
        opened.clear()
        api._capture_cursor()
        assert opened == ['beta'], 'a moved wal-index header is reread'
    finally:
        os.pwrite(fd, header, 0)
        os.close(fd)


_DELAYED_WRITER = r'''
import sqlite3, sys
c = sqlite3.connect(sys.argv[1], isolation_level=None)
c.execute("PRAGMA synchronous=FULL")
c.execute("PRAGMA wal_autocheckpoint=0")
c.execute("INSERT INTO task_comments(task_id, author, body, created_at) VALUES ('t_x', 'warm', 'b', 1)")
print("warm", flush=True)
sys.stdin.readline()
c.execute("BEGIN IMMEDIATE")
c.execute("INSERT INTO task_comments(task_id, author, body, created_at) VALUES ('t_x', 'probe', 'b', 1)")
print("committing", flush=True)
c.execute("COMMIT")
print("committed", flush=True)
sys.stdin.readline()
'''


def _strace_injects():
    import shutil
    import subprocess
    if not shutil.which('strace'):
        return False
    try:
        return subprocess.run(['strace', '-qq', '-e', 'inject=fsync:delay_enter=1', 'true'],
                              capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@pytest.mark.skipif(not _strace_injects(), reason='needs strace with ptrace and syscall delay injection')
def test_a_commit_made_visible_after_the_settle_window_is_seen(board, api, tmp_path):
    # Real writer, synchronous=FULL: strace delays the probe commit's WAL fsync,
    # so the commit becomes visible (-shm header) seconds after its last -wal
    # write. A capture inside that window must not pin the pre-commit revision.
    import subprocess
    import sys
    import time
    kb.create_board('beta')
    meta = next(b for b in kb.list_boards() if b['slug'] == 'beta')
    path = str(Path(meta['db_path']))
    script = tmp_path / 'writer.py'
    script.write_text(_DELAYED_WRITER)
    proc = subprocess.Popen(
        ['strace', '-f', '-qq', '-o', str(tmp_path / 'strace.log'), '-e', 'trace=fsync,fdatasync',
         '-e', 'inject=fsync,fdatasync:delay_enter=3000000:when=2+', sys.executable, str(script), path],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == 'warm'
        time.sleep(1.2)
        api._capture_cursor()
        before = dict(e for e in api._capture_cursor()[0] if isinstance(e, tuple) and len(e) == 2)[path]
        proc.stdin.write('\n'); proc.stdin.flush()
        assert proc.stdout.readline().strip() == 'committing'
        time.sleep(1.8)  # last -wal write is now older than SIBLING_SETTLE_NS
        assert proc.poll() is None, 'writer is still inside the delayed fsync'
        api._capture_cursor()
        assert proc.stdout.readline().strip() == 'committed'
        after = dict(e for e in api._capture_cursor()[0] if isinstance(e, tuple) and len(e) == 2)[path]
        assert after != before, 'a committed sibling write must move the cursor'
    finally:
        proc.stdin.write('\n'); proc.stdin.flush()
        proc.wait(10)


_LATER = 1 << 62  # a capture clock far past any write: every stamp is settled


def _wal_db(path):
    import sqlite3
    c = sqlite3.connect(str(path), isolation_level=None)
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('PRAGMA wal_autocheckpoint=0')
    c.execute('CREATE TABLE t(x)')
    c.execute('INSERT INTO t VALUES (1)')
    return c


def _other(path, code):
    import subprocess
    import sys
    return subprocess.run([sys.executable, '-c', 'import sqlite3,sys\n' + code, str(path)],
                          capture_output=True, text=True, timeout=30)


def test_stamping_keeps_a_same_process_writers_wal_lock(api, tmp_path):
    # POSIX drops every fcntl lock a process holds on an inode when any fd for
    # it closes; SQLite's WAL write lock lives on -shm. Stamping a sibling must
    # never let another process take the write lock this process holds.
    path = tmp_path / 'w.db'
    a = _wal_db(path)
    a.execute('BEGIN IMMEDIATE')
    try:
        for _ in range(3):
            assert api._settled_stamp(str(path), now_ns=_LATER)[-1] is not None
        r = _other(path, 'c = sqlite3.connect(sys.argv[1], timeout=0, isolation_level=None)\n'
                         'c.execute("BEGIN IMMEDIATE"); c.execute("INSERT INTO t VALUES (2)"); c.execute("COMMIT")\n'
                         'print("ACQUIRED")')
        assert 'ACQUIRED' not in r.stdout and 'locked' in r.stderr, r.stdout + r.stderr
        a.execute('INSERT INTO t VALUES (3)')
        a.execute('COMMIT')
    finally:
        a.close()


def test_stamping_keeps_a_same_process_read_snapshot(api, tmp_path):
    # A live read snapshot's read mark on -shm must keep a checkpoint from
    # truncating the WAL under it after a stamp.
    import sqlite3
    path = tmp_path / 'r.db'
    _wal_db(path).close()
    keep = sqlite3.connect(str(path), isolation_level=None)  # keeps -shm in WAL mode
    b = sqlite3.connect(str(path), isolation_level=None)
    b.execute('PRAGMA query_only=1')
    try:
        b.execute('BEGIN')
        assert b.execute('SELECT count(*) FROM t').fetchone()[0] == 1
        assert _other(path, 'sqlite3.connect(sys.argv[1], isolation_level=None)'
                            '.execute("INSERT INTO t VALUES (2)")').returncode == 0
        assert api._settled_stamp(str(path), now_ns=_LATER)[-1] is not None
        r = _other(path, 'c = sqlite3.connect(sys.argv[1], isolation_level=None)\n'
                         'print(c.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0])')
        assert r.stdout.strip() == '1', 'checkpoint must stay busy under the live snapshot: ' + r.stdout + r.stderr
        assert b.execute('SELECT count(*) FROM t').fetchone()[0] == 1
    finally:
        b.close()
        keep.close()


def _fds_on(target):
    # fds of this process on the file (an unlinked inode reads '<path> (deleted)').
    fds = '/proc/self/fd'
    if not os.path.isdir(fds):
        pytest.skip('needs /proc/self/fd')
    found = 0
    target = os.path.realpath(target)
    for name in os.listdir(fds):
        try:
            link = os.readlink(os.path.join(fds, name))
        except OSError:
            continue
        found += link in (target, target + ' (deleted)')
    return found


def test_shm_fds_are_reused_and_released_with_the_inode(api, tmp_path):
    # One retained fd per linked -shm inode: repeated stamps add none, and a
    # -shm that SQLite unlinks on last close (then recreates) is let go.
    path = tmp_path / 'x.db'
    shm = str(path) + '-shm'
    c = _wal_db(path)
    first = api._settled_stamp(str(path), now_ns=_LATER)
    base = _fds_on(shm)  # SQLite's own fd plus the one retained here
    assert base == 2
    for _ in range(20):
        api._settled_stamp(str(path), now_ns=_LATER)
    assert _fds_on(shm) == base
    assert [e[:2] for e in api._shm_fds[shm]] == [first[-1][:2]]
    c.close()  # last connection: checkpoint, then -wal/-shm are deleted
    assert not os.path.exists(shm)
    assert api._settled_stamp(str(path), now_ns=_LATER)[-1] is None
    assert shm not in api._shm_fds, 'the unlinked inode fd is closed'
    assert _fds_on(shm) == 0
    import sqlite3
    c = sqlite3.connect(str(path), isolation_level=None)
    c.execute('INSERT INTO t VALUES (2)')
    try:
        again = api._settled_stamp(str(path), now_ns=_LATER)
        new_ino = os.stat(shm).st_ino
        assert again[-1][1] == new_ino
        assert [e[1] for e in api._shm_fds[shm]] == [new_ino]
        assert _fds_on(shm) == base
    finally:
        c.close()


def test_a_retained_fd_number_reused_elsewhere_is_never_closed(api, tmp_path):
    # If the retained fd is closed by someone else and its number is reused by
    # an unrelated unlinked file, the stamp must forget it, not close that fd.
    path = tmp_path / 'y.db'
    shm = str(path) + '-shm'
    c = _wal_db(path)
    try:
        api._settled_stamp(str(path), now_ns=_LATER)
        (fd,) = [e[2] for e in api._shm_fds[shm]]
        os.close(fd)
        other = tmp_path / 'foreign'
        foreign = os.open(str(other), os.O_RDWR | os.O_CREAT)
        if foreign != fd:
            os.close(foreign)
            pytest.skip('fd number not reused')
        os.unlink(other)  # st_nlink == 0 on the foreign fd
        try:
            api._settled_stamp(str(path), now_ns=_LATER)
            os.fstat(foreign)  # still open: raises if the stamp closed it
            assert foreign not in [e[2] for e in api._shm_fds.get(shm, [])]
        finally:
            os.close(foreign)
    finally:
        c.close()


def test_an_unlisted_boards_shm_fd_goes_once_unlinked_and_not_before(api, tmp_path):
    # A board dropped from the listing keeps its fd while its -shm is linked
    # (a connection here may lock it) and loses it once SQLite unlinks -shm.
    path = tmp_path / 'z.db'
    shm = str(path) + '-shm'
    c = _wal_db(path)
    api._settled_stamp(str(path), now_ns=_LATER)
    try:
        api._release_unlisted_shm(set())
        assert shm in api._shm_fds and _fds_on(shm) == 2
    finally:
        c.close()
    assert not os.path.exists(shm)
    api._release_unlisted_shm(set())
    assert shm not in api._shm_fds
    assert _fds_on(shm) == 0


def test_a_replaced_sibling_is_reread(board, api, monkeypatch):
    kb.create_board('beta')
    meta = next(b for b in kb.list_boards() if b['slug'] == 'beta')
    opened = _counting(api, monkeypatch)
    _settle(api, monkeypatch)
    _warm(api)
    opened.clear()
    path = Path(meta['db_path'])
    copy = path.with_name('replacement.db')
    copy.write_bytes(path.read_bytes())
    os.replace(copy, path)
    api._capture_cursor()
    assert opened == ['beta']
