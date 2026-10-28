"""Tests for sqlite-live-guard. Run: python3 -m pytest -q -p no:cacheprovider tests_guard.py

The blocked commands are the ones agents ran against the hub's live default kanban.db on 2026-10-28
before it corrupted (rm of the -shm, raw sqlite3 writes and REINDEX, three concurrent mv swaps).
"""

import importlib.util
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


plugin = _load("sqlite_live_guard_plugin", "__init__.py")
swap_db = _load("sqlite_live_guard_swap", "swap_db.py")


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_SQLITE_GUARD", raising=False)
    monkeypatch.setenv("HOME", "/home/u")
    monkeypatch.delenv("HERMES_HOME", raising=False)


def blocked(tool, **args):
    return plugin._pre_tool_call(tool_name=tool, args=args) is not None


INCIDENT = [
    "rm -f /home/u/.hermes/kanban.db-shm",
    "rm -f ~/.hermes/kanban.db-wal ~/.hermes/kanban.db-shm",
    "cd ~/.hermes; rm -f kanban.db-shm",
    "cd ~/.hermes && cp -p kanban.db kanban.db.corrupt.manual-X.bak; mv -f kanban.db.new kanban.db",
    "cd ~/.hermes; mv kanban.db kanban.db.pre-recover-X",
    "mv ~/.hermes/kanban.db.recovered.X ~/.hermes/kanban.db",
    "cp recovered.db $HOME/.hermes/kanban.db",
    "cp recovered.db ${HERMES_HOME}/kanban.db",
    'cd ~/.hermes && sqlite3 -cmd ".timeout 10000" kanban.db "REINDEX idx_events_run;"',
    'sqlite3 /home/u/.hermes/kanban.db "PRAGMA wal_checkpoint(TRUNCATE)"',
    "cd ~/.hermes; sqlite3 kanban.db 'pragma wal_checkpoint' 2>&1 || true",
    "sqlite3 ~/.hermes/state.db 'VACUUM'",
    "sudo -u u truncate -s 0 /home/u/.hermes/state.db-wal",
    ": > ~/.hermes/kanban.db-wal",
    "dd if=/dev/zero of=/home/u/.hermes/kanban.db bs=1 count=0",
    "timeout 30 rm /home/u/.hermes/inbox/inbox.db-shm",
]

ALLOWED = [
    "sqlite3 -readonly ~/.hermes/kanban.db 'pragma quick_check'",
    "sqlite3 ~/.hermes/kanban.db 'select count(*) from tasks'",
    "sqlite3 'file:/home/u/.hermes/kanban.db?immutable=1' '.recover' > recover.sql",
    "sqlite3 'file:/home/u/.hermes/kanban.db?mode=ro' 'select 1'",
    "cp -p ~/.hermes/kanban.db ~/.hermes/cache/scratch/k/kanban.db",
    "rm -f ~/.hermes/kanban.db.corrupt.0ead89b6583bea60.bak-wal",
    "rm -f ~/.hermes/cache/scratch/kbrec/recovered.db ~/.hermes/cache/scratch/kbrec/kanban.db-wal",
    "mv ~/.hermes/kanban.db.new ~/.hermes/kanban-recovery-20261028/parked.db",
    "sqlite3 recovered.db < recover.sql",
    "rm -f kanban.db-wal",  # relative, cwd not the Hermes home
    "ls -la ~/.hermes/kanban.db*; stat ~/.hermes/kanban.db-wal",
    "python3 ~/.hermes/plugins/sqlite-live-guard/swap_db.py --db ~/.hermes/kanban.db --replacement r.db",
    "rm -rf /tmp/pytest-of-u/pytest-1/test_x/.hermes/kanban.db-wal",
    "echo hello > out.txt",
    # reads whose SQL merely contains write words
    "sqlite3 ~/.hermes/state.db \"select substr(replace(content, char(10), ' '),1,80) from messages\"",
    "sqlite3 ~/.hermes/kanban.db \"select * from task_events where kind like '%delete%'\"",
    "cd ~/.hermes; sqlite3 kanban.db \"pragma integrity_check; pragma journal_mode; select count(*) from tasks\"",
    "sqlite3 'file:/home/u/.hermes/state.db?mode=rw' 'PRAGMA query_only=ON; SELECT id FROM sessions'",
    # DML into a healthy (never quarantined) board: SQLite locks it correctly
    "sqlite3 ~/.hermes/kanban.db \"update tasks set max_retries=5 where id='t_x'\"",
]


def test_dml_blocked_only_on_recently_quarantined_board(monkeypatch, tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    db = home / "kanban.db"
    db.write_bytes(b"")
    cmds = [f"sqlite3 {db} \"INSERT INTO task_comments (task_id, body) VALUES ('t', 'x')\"",
            f"sqlite3 {db} \"UPDATE tasks SET status='ready' WHERE id='t'\"",
            f"sqlite3 {db} < fix.sql",
            "cd $HERMES_HOME; sqlite3 kanban.db \"UPDATE tasks SET status='ready'\""]
    for cmd in cmds:
        assert not blocked("terminal", command=cmd), cmd
    (home / "kanban.db.corrupt.5b38b0b55c71af7f.bak").write_bytes(b"x")
    for cmd in cmds:
        assert blocked("terminal", command=cmd), cmd
    old = time.time() - 3 * 3600
    os.utime(home / "kanban.db.corrupt.5b38b0b55c71af7f.bak", (old, old))
    assert not blocked("terminal", command=cmds[0])
    assert not blocked("terminal", command=f"sqlite3 {db} 'select count(*) from tasks'")


@pytest.mark.parametrize("cmd", INCIDENT)
def test_terminal_blocks_incident_commands(cmd):
    assert blocked("terminal", command=cmd), cmd


@pytest.mark.parametrize("cmd", ALLOWED)
def test_terminal_allows_reads_copies_and_backups(cmd):
    assert not blocked("terminal", command=cmd), cmd


def test_terminal_workdir_is_hermes_home():
    assert blocked("terminal", command="rm -f kanban.db-shm", workdir="/home/u/.hermes")
    assert not blocked("terminal", command="rm -f kanban.db-shm", workdir="/home/u/code/x")


def test_execute_code_path():
    assert blocked("execute_code", code="import os\nos.remove('/home/u/.hermes/kanban.db-wal')")
    assert blocked("execute_code", code="import shutil\nshutil.move('r.db', '/home/u/.hermes/kanban.db')")
    assert blocked("execute_code", code="import os\nos.system('rm -f /home/u/.hermes/kanban.db-shm')")
    assert not blocked("execute_code", code="import sqlite3\nsqlite3.connect('/home/u/.hermes/kanban.db')")
    assert not blocked("execute_code", code="import os\nos.remove('/home/u/.hermes/cache/scratch/x.db')")
    assert not blocked("execute_code", code="p = '/home/u/.hermes/state.db'\nprint(p.replace('a', 'b'))")
    assert not blocked("execute_code", code="import shutil\nshutil.copy2('/home/u/.hermes/kanban.db', '/tmp/k.db')")
    assert blocked("execute_code", code="from pathlib import Path\nPath('/home/u/.hermes/kanban.db-shm').unlink()")


def test_nested_tool_call_path():
    assert blocked("tool_call", calls=[{"name": "terminal", "arguments": {"command": "rm ~/.hermes/kanban.db-shm"}}])
    assert not blocked("tool_call", calls=[{"name": "terminal", "arguments": {"command": "ls ~/.hermes"}}])
    assert not blocked("read_file", path="/home/u/.hermes/kanban.db-wal")


def test_disable_env(monkeypatch):
    monkeypatch.setenv("HERMES_SQLITE_GUARD", "off")
    assert not blocked("terminal", command="rm -f ~/.hermes/kanban.db-shm")


def test_message_points_to_helper():
    out = plugin._pre_tool_call(tool_name="terminal", args={"command": "rm -f ~/.hermes/kanban.db-shm"})
    assert out["action"] == "block" and "swap_db.py" in out["message"] and "SQLITE LIVE GUARD" in out["message"]


def test_claude_hook():
    hook = HERE / "claude_hook.py"
    env = {**os.environ, "HOME": "/home/u"}
    bad = subprocess.run([sys.executable, hook], input='{"tool_input": {"command": "rm ~/.hermes/kanban.db-wal"}}',
                         capture_output=True, text=True, env=env)
    assert bad.returncode == 2 and "SQLITE LIVE GUARD" in bad.stderr
    ok = subprocess.run([sys.executable, hook], input='{"tool_input": {"command": "ls ~/.hermes"}}',
                        capture_output=True, text=True, env=env)
    assert ok.returncode == 0


# --- swap_db.py --------------------------------------------------------------------------------


def _db(path, rows):
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=wal")
    con.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY)")
    con.executemany("INSERT OR IGNORE INTO tasks VALUES (?)", [(r,) for r in rows])
    con.commit()
    con.close()


def test_swap_puts_replacement_in_and_keeps_originals(tmp_path):
    live, rep = tmp_path / "kanban.db", tmp_path / "recovered.db"
    _db(live, ["a"])
    _db(rep, ["a", "b"])
    assert swap_db.main(["--db", str(live), "--replacement", str(rep), "--wait", "0"]) == 0
    con = sqlite3.connect(live)
    assert [r[0] for r in con.execute("select id from tasks order by id")] == ["a", "b"]
    con.close()
    kept = sorted(p.name for p in tmp_path.glob("kanban.db.pre-swap-*"))
    assert len(kept) >= 1 and any(n.endswith("Z") for n in kept), kept  # main file kept (+ its -wal/-shm)
    old = sqlite3.connect(f"file:{tmp_path / [n for n in kept if n.endswith('Z')][0]}?mode=ro", uri=True)
    assert [r[0] for r in old.execute("select id from tasks")] == ["a"]
    old.close()


def test_swap_refuses_while_a_process_holds_the_db(tmp_path):
    live, rep = tmp_path / "kanban.db", tmp_path / "recovered.db"
    _db(live, ["a"])
    _db(rep, ["a", "b"])
    child = subprocess.Popen([sys.executable, "-c",
                              f"import sqlite3,time; c=sqlite3.connect({str(live)!r}); c.execute('select 1 from tasks')"
                              ".fetchall(); print('ready', flush=True); time.sleep(30)"], stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        assert swap_db.main(["--db", str(live), "--replacement", str(rep), "--wait", "0.5"]) == 2
        con = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
        assert [r[0] for r in con.execute("select id from tasks")] == ["a"]  # untouched
        con.close()
    finally:
        child.kill()
        child.wait()


def test_swap_refuses_second_concurrent_recovery(tmp_path):
    import fcntl
    live, rep = tmp_path / "kanban.db", tmp_path / "recovered.db"
    _db(live, ["a"])
    _db(rep, ["a", "b"])
    with open(tmp_path / "kanban.db.recovery.lock", "a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert swap_db.main(["--db", str(live), "--replacement", str(rep), "--wait", "0"]) == 2


def test_swap_refuses_corrupt_or_incomplete_replacement(tmp_path):
    live, rep = tmp_path / "kanban.db", tmp_path / "recovered.db"
    _db(live, ["a"])
    con = sqlite3.connect(live)
    con.execute("CREATE TABLE task_runs (id INTEGER PRIMARY KEY)")
    con.commit()
    con.close()
    _db(rep, ["a"])  # lacks task_runs
    assert swap_db.main(["--db", str(live), "--replacement", str(rep), "--wait", "0"]) == 2
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"SQLite format 3\x00" + b"\x00" * 4000)
    assert swap_db.main(["--db", str(live), "--replacement", str(bad), "--wait", "0"]) == 2
    time.sleep(0)
