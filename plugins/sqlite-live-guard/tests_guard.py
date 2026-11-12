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
    monkeypatch.setenv("HERMES_SQLITE_GUARD_LOG", str(tmp_path / "guard.log"))  # never the real log


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
    # DML into a healthy (never quarantined) non-Kanban database: SQLite locks it correctly
    "sqlite3 ~/.hermes/state.db \"update sessions set title='x' where id='s_x'\"",
    # the read-only probe the recovery procedure names
    "sqlite3 -readonly /home/u/.hermes/kanban.db 'pragma integrity_check'",
    "ls -la ~/.hermes/kanban.db.corrupt.*.bak",
    "cp ~/.hermes/kanban.db.corrupt.5b38b0b55c71af7f.bak ~/.hermes/cache/scratch/inspect.db",
]


def test_kanban_dml_blocked_even_without_a_quarantine_and_other_db_dml_only_when_quarantined(monkeypatch, tmp_path):
    """Raw DML into a live Kanban board is refused whether or not a quarantine exists: deleting the
    ``.corrupt.*.bak`` once switched this block off (self-heal agent-6eb3a51e94). Other live databases keep
    the quarantine rule."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    db = home / "kanban.db"
    db.write_bytes(b"")
    cmds = [f"sqlite3 {db} \"INSERT INTO task_comments (task_id, body) VALUES ('t', 'x')\"",
            f"sqlite3 {db} \"UPDATE tasks SET status='ready' WHERE id='t'\"",
            f"sqlite3 {db} < fix.sql",
            "cd $HERMES_HOME; sqlite3 kanban.db \"UPDATE tasks SET status='ready'\"",
            "sqlite3 ~/.hermes/kanban.db \"update tasks set max_retries=5 where id='t_x'\"",
            f"sqlite3 {home}/kanban/boards/ops/kanban.db \"DELETE FROM task_links\""]
    for cmd in cmds:
        assert blocked("terminal", command=cmd), cmd
    other = home / "state.db"
    other.write_bytes(b"")
    write = f"sqlite3 {other} \"UPDATE sessions SET title='x'\""
    assert not blocked("terminal", command=write)
    (home / "state.db.corrupt.5b38b0b55c71af7f.bak").write_bytes(b"x")
    assert blocked("terminal", command=write)
    old = time.time() - 3 * 3600
    os.utime(home / "state.db.corrupt.5b38b0b55c71af7f.bak", (old, old))
    assert not blocked("terminal", command=write)
    assert not blocked("terminal", command=f"sqlite3 {db} 'select count(*) from tasks'")


# The exact commands session 20261027_085427_ac4078 ran on the hub at 2026-10-28 03:09-03:10Z after five
# kanban tool calls returned `disk I/O error` (state.db messages 196878-196916). Before this change only the
# `rm -f kanban.db-shm` one was refused.
SESSION_6EB3 = [
    'ps -p 384195 -o pid,comm,args 2>&1; echo "---"; sqlite3 /home/ubuntu/.hermes/kanban.db "INSERT INTO '
    "task_comments (task_id, author, body, created_at) VALUES ('t_01a17e8a62a6ef7b', 'default', 'ARCHITECTURE "
    "DECISION: terraform-aws-github-runner chosen.', strftime('%s','now'))\" 2>&1",
    "# Try updating status directly since the kanban tool has a stale corruption flag\n"
    "sqlite3 /home/ubuntu/.hermes/kanban.db \"UPDATE tasks SET status='ready', block_kind=NULL, "
    "human_request=NULL WHERE id='t_01a17e8a62a6ef7b' AND status='blocked'\" 2>&1\necho \"exit=$?\"\n"
    "sqlite3 /home/ubuntu/.hermes/kanban.db \"SELECT id, status, block_kind FROM tasks WHERE "
    "id='t_01a17e8a62a6ef7b'\" 2>&1",
    "# The corrupt backup file created at 03:07 is what triggers the refusal.\n"
    "# The tool checks for .corrupt.*.bak files. Remove the newest one since the DB passes integrity_check.\n"
    "rm /home/ubuntu/.hermes/kanban.db.corrupt.5b38b0b55c71af7f.bak "
    "/home/ubuntu/.hermes/kanban.db.corrupt.c9de721bd45c3e93.bak\necho \"removed newest corrupt markers\"\n"
    "ls /home/ubuntu/.hermes/kanban.db.corrupt.*.bak 2>&1 | wc -l",
    "# The SHM file might have stale corruption markers. Remove it to force re-creation.\n"
    "rm -f /home/ubuntu/.hermes/kanban.db-shm\n# Verify DB still accessible\n"
    "sqlite3 /home/ubuntu/.hermes/kanban.db \"SELECT count(*) FROM tasks\" 2>&1\necho \"---\"\n"
    "# Also remove ALL corrupt backup files since integrity_check passes\n"
    "rm -f /home/ubuntu/.hermes/kanban.db.corrupt.*.bak\necho \"removed all corrupt markers\"",
    "sqlite3 /home/ubuntu/.hermes/kanban.db \"INSERT INTO task_comments (task_id, author, body, created_at) "
    "VALUES ('t_01a17e8a62a6ef7b', 'default', 'UPDATED CUTOVER PLAN', strftime('%s','now'))\"",
    "# Add unblock event to task_events so dispatcher sees the transition\n"
    "sqlite3 /home/ubuntu/.hermes/kanban.db \"INSERT INTO task_events (task_id, kind, payload, created_at, "
    "run_id) VALUES ('t_01a17e8a62a6ef7b', 'unblocked', NULL, strftime('%s','now'), NULL)\" 2>&1\n"
    "echo \"exit=$?\"",
]


@pytest.mark.parametrize("cmd", SESSION_6EB3)
def test_session_6eb3_board_writes_and_quarantine_deletes_are_refused(cmd):
    assert blocked("terminal", command=cmd), cmd


@pytest.mark.parametrize("cmd", [
    "rm -f /home/u/.hermes/kanban.db.corrupt.5b38b0b55c71af7f.bak",
    "rm ~/.hermes/kanban.db.corrupt.*.bak",
    "cd ~/.hermes && rm kanban.db.corrupt.c9de721bd45c3e93.bak",
    "mv ~/.hermes/kanban.db.corrupt.c9de721bd45c3e93.bak /tmp/x",
    "truncate -s 0 ~/.hermes/kanban/boards/ops/kanban.db.corrupt.abc.bak",
    "rm -f ~/.hermes/state.db.corrupt.0123456789abcdef.bak",
])
def test_quarantine_backups_cannot_be_deleted_or_moved(cmd):
    assert blocked("terminal", command=cmd), cmd


def test_quarantine_delete_from_execute_code_is_refused():
    assert blocked("execute_code", code="import os\nos.remove('/home/u/.hermes/kanban.db.corrupt.5b38b0b55c71af7f.bak')")


@pytest.mark.parametrize("sql,verb", [
    ("INSERT INTO task_comments (task_id, body) VALUES ('t', 'x')", "hermes kanban comment"),
    ("UPDATE tasks SET status='ready' WHERE id='t'", "hermes kanban unblock"),
    ("INSERT INTO task_events (task_id, kind) VALUES ('t', 'unblocked')", "hermes kanban"),
    ("INSERT INTO task_links (parent_id, child_id) VALUES ('a', 'b')", "hermes kanban link"),
])
def test_board_write_refusal_names_the_matching_hermes_kanban_verb(sql, verb):
    why = plugin.guard.check_shell(f'sqlite3 /home/u/.hermes/kanban.db "{sql}"')
    assert why and verb in why, why
    msg = plugin.guard.message(why)
    assert verb in msg and "hermes kanban recover" in msg


def test_named_board_write_refusal_puts_the_board_flag_first():
    why = plugin.guard.check_shell("sqlite3 /home/u/.hermes/kanban/boards/ops/kanban.db \"INSERT INTO task_comments "
                                   "(task_id, body) VALUES ('t', 'x')\"")
    assert why and "hermes kanban --board ops comment" in why, why


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
    # non-Kanban databases: the locked swap helper (Kanban boards: `hermes kanban recover`, tested below)
    out = plugin._pre_tool_call(tool_name="terminal", args={"command": "rm -f ~/.hermes/state.db-shm"})
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


def _own_tree(entries=swap_db._proc_entries):
    """/proc entries of this test process and its descendants: the only possible fixture holders."""
    root, listed, parent = os.getpid(), list(entries()), {}
    for p in listed:
        if p.name.isdigit():
            try:
                parent[int(p.name)] = int((p / "stat").read_text().rsplit(")", 1)[1].split()[1])
            except (OSError, IndexError, ValueError):
                pass

    def mine(pid):
        seen = set()
        while pid and pid not in seen:
            if pid == root:
                return True
            seen.add(pid)
            pid = parent.get(pid)
        return False

    return [p for p in listed if p.name.isdigit() and mine(int(p.name))]


@pytest.fixture(autouse=True)
def privileged_fixture_scan(monkeypatch):
    # Privileged (CI): the real whole-host scan via sudo. Otherwise scope the scan to this test's own
    # process tree, so another user's unreadable pid (pid 1 on a non-root host) can't abort the fixture;
    # holders() itself still refuses any unreadable pid it is given.
    if os.environ.get('SWAP_TEST_PRIVILEGED_SCAN') == '1' or os.environ.get('GITHUB_ACTIONS') == 'true':
        real = swap_db._scan
        monkeypatch.setattr(swap_db, '_scan', lambda db, privileged=False, extra_paths=(): real(db, True, extra_paths))
    else:
        monkeypatch.setattr(swap_db, '_proc_entries', _own_tree)


def test_swap_puts_replacement_in_and_keeps_originals(tmp_path):
    live, rep = tmp_path / "kanban.db", tmp_path / "recovered.db"
    _db(live, ["a"])
    _db(rep, ["a", "b"])
    assert swap_db.main(["--db", str(live), "--replacement", str(rep), "--wait", "0", "--allow-kanban"]) == 0
    con = sqlite3.connect(live)
    assert [r[0] for r in con.execute("select id from tasks order by id")] == ["a", "b"]
    con.close()
    kept = list(tmp_path.glob('.kanban.db.recovery-*/original.db'))
    assert len(kept) == 1
    old = sqlite3.connect(f"file:{kept[0]}?mode=ro", uri=True)
    assert [r[0] for r in old.execute("select id from tasks")] == ["a"]
    old.close()


def test_swap_refuses_while_a_process_holds_the_db(tmp_path, capsys):
    live, rep = tmp_path / "kanban.db", tmp_path / "recovered.db"
    _db(live, ["a"])
    _db(rep, ["a", "b"])
    child = subprocess.Popen([sys.executable, "-c",
                              f"import sqlite3,time; c=sqlite3.connect({str(live)!r}); c.execute('select 1 from tasks')"
                              ".fetchall(); print('ready', flush=True); time.sleep(30)"], stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        assert swap_db.main(["--db", str(live), "--replacement", str(rep), "--wait", "0.5", "--allow-kanban"]) == 2
        assert f"pid {child.pid} " in capsys.readouterr().err  # refused for this holder, not an incomplete scan
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
        assert swap_db.main(["--db", str(live), "--replacement", str(rep), "--wait", "0", "--allow-kanban"]) == 2


def test_swap_refuses_corrupt_or_incomplete_replacement(tmp_path):
    live, rep = tmp_path / "kanban.db", tmp_path / "recovered.db"
    _db(live, ["a"])
    con = sqlite3.connect(live)
    con.execute("CREATE TABLE task_runs (id INTEGER PRIMARY KEY)")
    con.commit()
    con.close()
    _db(rep, ["a"])  # lacks task_runs
    assert swap_db.main(["--db", str(live), "--replacement", str(rep), "--wait", "0", "--allow-kanban"]) == 2
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"SQLite format 3\x00" + b"\x00" * 4000)
    assert swap_db.main(["--db", str(live), "--replacement", str(bad), "--wait", "0", "--allow-kanban"]) == 2
    time.sleep(0)


# --- kanban boards go through `hermes kanban recover` (t_01a180cfd048ffcc) -----------------------------


@pytest.fixture
def tmp_home(monkeypatch, tmp_path):
    """A real tmp HERMES_HOME (never the live board); guard log redirected into it."""
    home = tmp_path / ".hermes"
    (home / "kanban" / "boards" / "ops").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_SESSION_ID", "sess_guardtest")
    monkeypatch.delenv("HERMES_SQLITE_GUARD_LOG")  # default location: <tmp home>/logs/sqlite-live-guard.log
    return home


def _block(tool, **args):
    return plugin._pre_tool_call(tool_name=tool, args=args)


def test_acceptance_a_terminal_mv_over_the_board_is_refused(tmp_home):
    out = _block("terminal", command="mv /tmp/x ~/.hermes/kanban.db")
    assert out and out["action"] == "block"
    out = _block("terminal", command=f"mv /tmp/x {tmp_home}/kanban/boards/ops/kanban.db")
    assert out and out["action"] == "block"


def test_acceptance_b_execute_code_os_replace_over_the_board_is_refused(tmp_home):
    code = "import os\nos.replace('/tmp/rebuilt.db', os.path.expanduser('~/.hermes/kanban.db'))"
    assert _block("execute_code", code=code)


def test_acceptance_c_rm_of_the_wal_is_refused(tmp_home):
    assert _block("terminal", command="rm ~/.hermes/kanban.db-wal")


def test_acceptance_d_readonly_quick_check_and_copy_out_are_allowed(tmp_home):
    assert _block("terminal", command="sqlite3 -readonly ~/.hermes/kanban.db 'pragma quick_check'") is None
    assert _block("terminal", command="cp ~/.hermes/kanban.db /tmp/x") is None
    assert _block("terminal", command="hermes kanban recover") is None
    assert _block("terminal", command="hermes kanban --board ops recover --json") is None


def test_execute_code_path_built_from_the_home_is_refused(tmp_home):
    code = ("from pathlib import Path\nimport os\nhome = Path.home() / '.hermes'\n"
            "os.replace('/tmp/rebuilt.db', home / 'kanban.db')")
    assert _block("execute_code", code=code)
    code = "import shutil, os\nHOME = Path(os.environ['HERMES_HOME'])\nshutil.move(rebuilt, HOME / 'kanban.db-wal')"
    assert _block("execute_code", code=code)
    # reading the board, or moving a scratch file that only shares the name, stays allowed
    assert _block("execute_code", code="import sqlite3\nsqlite3.connect(str(home / 'kanban.db'))") is None
    assert _block("execute_code", code="import shutil\nshutil.copy2(home / 'kanban.db', '/tmp/k.db')") is None


def test_refusal_on_the_default_board_names_hermes_kanban_recover(tmp_home):
    out = _block("terminal", command="mv /tmp/x ~/.hermes/kanban.db")
    assert "hermes kanban recover" in out["message"]
    assert "swap_db.py" not in out["message"], "kanban boards never go through the rename-based swap helper"


def test_refusal_on_a_named_board_names_its_slug(tmp_home):
    out = _block("terminal", command=f"rm {tmp_home}/kanban/boards/ops/kanban.db-shm")
    assert "hermes kanban --board ops recover" in out["message"]


def test_refusal_names_the_live_recovery_lock_holder(tmp_home):
    import json
    (tmp_home / "kanban.db.recover.pid").write_text(json.dumps(
        {"pid": os.getpid(), "session_id": "sess_holder9", "task_id": "t_holder9", "started_at": time.time()}))
    out = _block("terminal", command="mv /tmp/x ~/.hermes/kanban.db")
    assert "recovery is running" in out["message"]
    assert f"pid {os.getpid()}" in out["message"] and "sess_holder9" in out["message"] and "t_holder9" in out["message"]


def test_refusal_ignores_a_holder_record_whose_process_is_gone(tmp_home):
    import json
    (tmp_home / "kanban.db.recover.pid").write_text(json.dumps({"pid": 2 ** 22 + 4321, "task_id": "t_dead"}))
    out = _block("terminal", command="mv /tmp/x ~/.hermes/kanban.db")
    assert "t_dead" not in out["message"] and "recovery is running" not in out["message"]


def test_non_kanban_database_refusal_still_points_to_the_swap_helper(tmp_home):
    out = _block("terminal", command="rm ~/.hermes/state.db-wal")
    assert "swap_db.py" in out["message"] and "hermes kanban recover" not in out["message"]


def test_every_refusal_is_appended_to_the_guard_log(tmp_home, monkeypatch):
    import json
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_logcard")
    _block("terminal", command="mv /tmp/x ~/.hermes/kanban.db")
    _block("terminal", command="ls ~/.hermes")  # allowed: not logged
    lines = (tmp_home / "logs" / "sqlite-live-guard.log").read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["tool"] == "terminal" and "kanban.db" in rec["reason"]
    assert rec["command"].startswith("mv /tmp/x") and rec["task_id"] == "t_logcard"
    assert rec["session_id"] == "sess_guardtest" and rec["at"]


def test_swap_helper_refuses_a_kanban_board_without_the_operator_flag(tmp_path, capsys):
    live, rep = tmp_path / "kanban.db", tmp_path / "recovered.db"
    _db(live, ["a"])
    _db(rep, ["a", "b"])
    assert swap_db.main(["--db", str(live), "--replacement", str(rep), "--wait", "0"]) == 2
    assert "hermes kanban recover" in capsys.readouterr().err
    con = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
    assert [r[0] for r in con.execute("select id from tasks")] == ["a"]
    con.close()


def test_swap_helper_records_itself_as_lock_holder(tmp_path, monkeypatch):
    import json
    live, rep = tmp_path / "fixture.db", tmp_path / "recovered.db"
    _db(live, ["a"])
    _db(rep, ["a", "b"])
    seen = {}
    real = swap_db._snapshot

    def spy(db, replacement):
        seen["record"] = json.loads((tmp_path / "fixture.db.recover.pid").read_text())
        return real(db, replacement)

    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_swapcard")
    monkeypatch.setattr(swap_db, "_snapshot", spy)
    assert swap_db.main(["--db", str(live), "--replacement", str(rep), "--wait", "0"]) == 0
    assert seen["record"]["pid"] == os.getpid() and seen["record"]["task_id"] == "t_swapcard"
    assert not (tmp_path / "fixture.db.recover.pid").exists(), "released lock leaves no holder record"


# --- review follow-ups ---------------------------------------------------------------------------------


@pytest.mark.parametrize("cmd", [
    "mv /tmp/kanban.db ~/.hermes/",
    "mv /tmp/kanban.db ~/.hermes",
    "cp /tmp/kanban.db ~/.hermes/",
    "rsync -a /tmp/kanban.db ~/.hermes/",
    "mv -t ~/.hermes /tmp/kanban.db",
    "cp --target-directory=$HOME/.hermes /tmp/rebuilt/kanban.db-wal",
    "install -m 644 /tmp/kanban.db ~/.hermes/kanban/boards/ops/",
    "cat /tmp/rebuilt.db | tee ~/.hermes/kanban.db >/dev/null",
    "sqlite3 /tmp/rebuilt.db '.backup ~/.hermes/kanban.db'",
    "sqlite3 /tmp/rebuilt.db \".save /home/u/.hermes/kanban.db\"",
    "python3 -c \"import os; os.replace('/tmp/r.db', os.path.expanduser('~/.hermes/kanban.db'))\"",
])
def test_review_swaps_into_the_home_directory_or_through_other_writers_are_refused(cmd):
    assert blocked("terminal", command=cmd), cmd


@pytest.mark.parametrize("cmd", [
    "cp ~/.hermes/kanban.db /tmp/",
    "mv /tmp/kanban.db /tmp/elsewhere/",
    "cp /tmp/notes.txt ~/.hermes/",
    "tee ~/.hermes/cache/scratch/out.log",
    "sqlite3 ~/.hermes/kanban.db '.backup /tmp/k.db'",
    "python3 -c \"import sqlite3; sqlite3.connect('/home/u/.hermes/kanban.db').execute('select 1')\"",
])
def test_review_reads_and_unrelated_copies_stay_allowed(cmd):
    assert not blocked("terminal", command=cmd), cmd


@pytest.mark.parametrize("code", [
    "import os\nhome = os.environ['HERMES_HOME']\nos.replace(x, os.path.join(home, 'kanban.db'))",
    "import os\nhome = Path(os.environ['HERMES_HOME'])\nos.replace(x, str(home / 'kanban.db'))",
    "import os\nhome = Path.home() / '.hermes'\ndst = home / 'kanban.db'\nos.replace(x, dst)",
    "from pathlib import Path\nhome = Path.home() / '.hermes'\nPath(x).replace(home / 'kanban.db')",
    "from pathlib import Path\nhome = Path.home() / '.hermes'\n(home / 'kanban.db-wal').unlink()",
    "from hermes_cli import kanban_db as kb\nimport os\nos.replace(x, kb.kanban_db_path())",
])
def test_review_execute_code_path_spellings_are_refused(code):
    assert blocked("execute_code", code=code), code


@pytest.mark.parametrize("code", [
    "import shutil, tempfile\nfrom pathlib import Path\ntmp = Path(tempfile.mkdtemp())\nshutil.copy(fx, tmp / 'kanban.db')",
    "import os\nos.remove(tmp_path / 'kanban.db-wal')",
    "import shutil\nshutil.copy2(home / 'kanban.db', '/tmp/k.db')",
    "print(\"os.replace(x, home / 'kanban.db')\")",
])
def test_review_execute_code_fixture_copies_outside_the_home_are_allowed(code):
    assert not blocked("execute_code", code=code), code


def test_review_profile_and_lookalike_boards_are_not_the_default_board(tmp_home):
    out = _block("terminal", command=f"rm {tmp_home}/profiles/p1/kanban.db-wal")
    assert out and "`hermes kanban recover`" not in out["message"]
    assert "HERMES_HOME=" in out["message"] and "profiles/p1" in out["message"]
    out = _block("terminal", command="rm ~/.hermes/my_kanban.db-wal")
    assert out and "swap_db.py" in out["message"] and "recover`" not in out["message"]


def test_review_holder_record_with_a_reused_pid_is_ignored(tmp_home):
    import json
    (tmp_home / "kanban.db.recover.pid").write_text(json.dumps(
        {"pid": os.getpid(), "task_id": "t_reused", "proc_start": "1"}))  # this process did not start at tick 1
    out = _block("terminal", command="mv /tmp/x ~/.hermes/kanban.db")
    assert "t_reused" not in out["message"]


def test_review_claude_hook_logs_its_refusals(tmp_path):
    hook = HERE / "claude_hook.py"
    log = tmp_path / "guard.log"
    env = {**os.environ, "HOME": "/home/u", "HERMES_SQLITE_GUARD_LOG": str(log)}
    bad = subprocess.run([sys.executable, hook], input='{"tool_input": {"command": "rm ~/.hermes/kanban.db-wal"}}',
                         capture_output=True, text=True, env=env, check=False)
    assert bad.returncode == 2
    assert '"tool": "claude:Bash"' in log.read_text()


def test_review_swap_helper_clears_its_record_when_the_replacement_is_refused(tmp_path):
    live, bad = tmp_path / "fixture.db", tmp_path / "bad.db"
    _db(live, ["a"])
    bad.write_bytes(b"SQLite format 3\x00" + b"\x00" * 4000)
    assert swap_db.main(["--db", str(live), "--replacement", str(bad), "--wait", "0"]) == 2
    assert not (tmp_path / "fixture.db.recover.pid").exists()


def test_review_swap_helper_records_the_recovery_holder_card_env(tmp_path, monkeypatch):
    import json
    live, rep = tmp_path / "fixture.db", tmp_path / "recovered.db"
    _db(live, ["a"])
    _db(rep, ["a", "b"])
    seen = {}
    real = swap_db._snapshot

    def spy(db, replacement):
        seen["record"] = json.loads((tmp_path / "fixture.db.recover.pid").read_text())
        return real(db, replacement)

    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.setenv("HERMES_RECOVERY_HOLDER_TASK", "t_viaholderenv")
    monkeypatch.setattr(swap_db, "_snapshot", spy)
    assert swap_db.main(["--db", str(live), "--replacement", str(rep), "--wait", "0"]) == 0
    assert seen["record"]["task_id"] == "t_viaholderenv" and seen["record"].get("proc_start")


def test_non_board_folders_under_boards_are_not_live(tmp_home):
    """``boards/_archived/`` is not a board (kanban-e808885f9e): a stray empty db there may be moved away."""
    assert _block("terminal", command="mv ~/.hermes/kanban/boards/_archived/kanban.db ~/.hermes/archive/x.db") is None
    assert _block("terminal", command="rm ~/.hermes/kanban/boards/_archived/refpin-1/kanban.db") is None
    assert _block("terminal", command="mv ~/.hermes/kanban/boards/ops/kanban.db /tmp/x")
    assert _block("terminal", command="mv ~/.hermes/kanban/boards/ops_2/kanban.db-wal /tmp/x")


def test_check_replacement_falls_back_to_query_only_when_ro_open_fails(tmp_path, monkeypatch):
    live, rep = tmp_path / "live.db", tmp_path / "rep.db"
    for p in (live, rep):
        c = sqlite3.connect(p); c.execute("create table t(x)"); c.execute("insert into t values (1)"); c.commit(); c.close()
    real = sqlite3.connect

    def connect(target, *a, **kw):
        if "mode=ro" in str(target):
            raise sqlite3.OperationalError("unable to open database file")
        return real(target, *a, **kw)
    monkeypatch.setattr(sqlite3, "connect", connect)
    # Input snapshots refuse rather than open the operator's file rw.
    # Only the private offline source copy gets query_only fallback.
    with pytest.raises(sqlite3.OperationalError):
        swap_db._snapshot(rep, tmp_path / 'staged.db')
    con = swap_db._ro_connect(rep)
    try:
        with pytest.raises(sqlite3.OperationalError):
            con.execute("insert into t values (2)")
    finally:
        con.close()
    with pytest.raises(sqlite3.OperationalError):
        swap_db._ro_connect(tmp_path / "missing.db")
    assert not (tmp_path / "missing.db").exists()


# A redirect is not an operand: `cp x ~/.hermes/state.db 2>/dev/null` took `/dev/null` as cp's destination and
# passed; `2>&1` left `1` there.
@pytest.mark.parametrize("cmd", ["cp recovered.db ~/.hermes/state.db 2>/dev/null",
                                 "cp recovered.db ~/.hermes/kanban.db 2>&1",
                                 "cp recovered.db ~/.hermes/kanban.db 2>>/tmp/e.log",
                                 "( cp recovered.db ~/.hermes/state.db 2>/dev/null )",
                                 "mv /tmp/k.db ~/.hermes/kanban.db &>/dev/null",
                                 "rm -f ~/.hermes/kanban.db-shm 2>/dev/null"])
def test_redirect_is_not_an_operand(cmd):
    assert blocked("terminal", command=cmd), cmd


@pytest.mark.parametrize("cmd", ["cp ~/.hermes/state.db /tmp/b.db 2>/dev/null", "cp ~/.hermes/state.db /tmp/b.db 2>&1",
                                 "rm -f /tmp/x.db 2>/dev/null", "sqlite3 ~/.hermes/state.db 'select 1' 2>/dev/null"])
def test_redirect_controls_allowed(cmd):
    assert not blocked("terminal", command=cmd), cmd


# A quoted `<`/`>` is an argument, not a redirect: `grep -c '>' ~/.hermes/state.db` was refused as a `>` redirect
# onto the db, and `rm -f '>' ~/.hermes/kanban.db` was judged by that phantom redirect instead of as an rm.
@pytest.mark.parametrize("cmd", ["grep -c '>' ~/.hermes/state.db", 'grep -c ">>" ~/.hermes/kanban.db',
                                 "grep -c \\> ~/.hermes/state.db", "cp ~/.hermes/state.db /tmp/b.db 2<&1",
                                 "cp ~/.hermes/state.db /tmp/b.db <&-"])
def test_quoted_redirect_and_input_dup_allowed(cmd):
    assert not blocked("terminal", command=cmd), cmd


@pytest.mark.parametrize("cmd", ["rm -f '>' ~/.hermes/kanban.db", "echo '>' > ~/.hermes/state.db",
                                 "cp /tmp/b.db ~/.hermes/state.db 2<&1", "cp /tmp/b.db ~/.hermes/state.db <&-"])
def test_quoted_redirect_and_input_dup_refused(cmd):
    assert blocked("terminal", command=cmd), cmd


def test_quoted_redirect_is_an_rm_operand():
    why = plugin.guard.check_shell("rm -f '>' ~/.hermes/kanban.db")
    assert why == "`rm` would delete or truncate live database file ~/.hermes/kanban.db"


# Bash runs a command substitution inside double quotes (and in backticks) like an unquoted one, but shlex kept
# `"$(...)"` as one data word: `out="$(rm -f ~/.hermes/state.db 2>&1)"` passed while `out=$(rm -f ...)` was refused.
RM_STATE = "`rm` would delete or truncate live database file ~/.hermes/state.db"
REDIR_STATE = "a `>` redirect would overwrite live database file ~/.hermes/state.db"


@pytest.mark.parametrize("cmd,why", [
    ("echo $(rm -f ~/.hermes/state.db)", RM_STATE), ('echo "$(rm -f ~/.hermes/state.db)"', RM_STATE),
    ("out=$(rm -f ~/.hermes/state.db 2>&1)", RM_STATE), ('out="$(rm -f ~/.hermes/state.db 2>&1)"', RM_STATE),
    ("echo `rm -f ~/.hermes/state.db`", RM_STATE), ('echo "`rm -f ~/.hermes/state.db`"', RM_STATE),
    ("x=$(echo hi > ~/.hermes/state.db)", REDIR_STATE), ('echo "$(echo hi > ~/.hermes/state.db)"', REDIR_STATE),
    ('echo "$(echo "$(rm -f ~/.hermes/state.db)")"', RM_STATE)])
def test_double_quoted_substitution_runs_like_unquoted(cmd, why):
    assert plugin.guard.check_shell(cmd) == why, cmd
    assert blocked("terminal", command=cmd), cmd


@pytest.mark.parametrize("cmd", ["echo '$(rm -f ~/.hermes/state.db)'", 'echo "\\$(rm -f ~/.hermes/state.db)"',
                                 'echo "\\`rm -f ~/.hermes/state.db\\`"', 'out="$(git status 2>&1)"',
                                 'out="$(ls ~/.hermes 2>/dev/null)"',
                                 'n="$(sqlite3 ~/.hermes/state.db \'select count(*) from sessions\' 2>&1)"'])
def test_inert_substitution_shapes_stay_allowed(cmd):
    assert plugin.guard.check_shell(cmd) is None, cmd
    assert not blocked("terminal", command=cmd), cmd


def test_double_quoted_substitution_execute_code():
    assert blocked("execute_code", code="import os\nos.system('out=\"$(rm -f ~/.hermes/state.db 2>&1)\"')")
    assert blocked("execute_code", code="import subprocess\nsubprocess.run('echo \"`rm -f ~/.hermes/kanban.db`\"', "
                                        "shell=True)")
    # (no single-quoted negative here: check_python's regex literal scan already reads the inner '...' of
    # os.system("echo '$(rm ...)'") as its own command, a pre-existing over-approximation of that path)
    assert not blocked("execute_code", code="import os\nos.system('out=\"$(git status 2>&1)\"')")


# Heredoc bodies and `#` comments are data (card t_01a1d020a0b79528). An apostrophe in either (`it's`, `don't`)
# unbalanced the quotes, so the lexer gave up on the whole command and a command AFTER it ran unchecked; the
# `$(...)` of an unquoted-delimiter body runs too, even beside an apostrophe. Shapes 1-7 are the card's probe.
HEREDOC_SHAPES = [
    "{c}",
    "cat > /tmp/n.txt <<EOF\nall done\nEOF\n{c}",
    "cat > /tmp/n.txt <<EOF\nit's done\nEOF\n{c}",
    "cat > /tmp/n.txt <<'EOF'\nit's done\nEOF\n{c}",
    "# don't skip this\n{c}",
    "cat > /tmp/n.txt <<EOF\nok $({c})\nEOF",
    "cat > /tmp/n.txt <<EOF\nit's $({c})\nEOF",
    "cat > /tmp/n.txt <<-EOF\n\tit's done\n\tEOF\n{c}",
    "cat > /tmp/a <<A; cat > /tmp/b <<'B'\nit's a\nA\nit's b\nB\n{c}",
    "cat > /tmp/n.txt <<\\EOF\nit's\nEOF\n{c}",
    "cat > /tmp/n.txt <<\"EOF\"\nit's\nEOF\n{c}",
    "cat > /tmp/n.txt <<EOF\nit's `{c}`\nEOF",
    "echo hi # it's a note\n{c}",
    "{c}\necho don't",
    "bash <<'EOF'\n# it's here\n{c}\nEOF",
]
HEREDOC_ALLOWED = [
    "cat > /tmp/n.txt <<EOF\nit's done\nEOF\n{safe}",
    "# don't worry\n{safe}",
    "cat > /tmp/n.txt <<EOF\nit's $(date)\nEOF",
    "cat > /tmp/n.txt <<'EOF'\nit's $({c})\nEOF",
    "cat > /tmp/n.txt <<EOF\nit's \\$({c})\nEOF",
]


@pytest.mark.parametrize("shape", HEREDOC_SHAPES)
def test_heredoc_and_comment_apostrophes_hide_no_command(shape):
    cmd = shape.format(c="rm -f ~/.hermes/state.db")
    assert plugin.guard.check_shell(cmd) == RM_STATE, cmd
    assert blocked("terminal", command=cmd), cmd


@pytest.mark.parametrize("shape", HEREDOC_ALLOWED)
def test_heredoc_and_comment_data_stays_allowed(shape):
    cmd = shape.format(c="rm -f ~/.hermes/state.db", safe="ls plugins")
    assert plugin.guard.check_shell(cmd) is None, cmd
    assert not blocked("terminal", command=cmd), cmd


# A heredoc body is data only when every reader is known to read it as data (cat, tee, grep, git, python, ...).
# A body fed to anything else may run as shell code: `su -`, `sudo -s`/`-i`, `source /dev/stdin`, `"$SHELL"`,
# `eval`, `exec bash`, `xargs sh`, a pipe into `sudo -s`, or an unknown reader. Review of 77ac105f found those
# read as data (fail open); they are code again (card t_01a1d020a0b79528). `<<` inside `${...}` is no heredoc.
# Review of df783b71: a body read inside a command substitution is data only when the substitution's output
# goes to a data consumer (git/gh args, an assignment); `bash -c "$(cat <<'EOF' ...)"`, `eval "$(cat ...)"`
# and a reader on a line with `>(bash)` run it. So does a script written from a body and then run (`bash s.sh`).
READER_SHAPES = [
    "su - <<EOF\n{c}\nEOF",
    "sudo -s <<EOF\n{c}\nEOF",
    "sudo -i <<'EOF'\necho here\n{c}\nEOF",
    "sudo --login <<EOF\n{c}\nEOF",
    "sudo -u root bash <<'EOF'\n{c}\nEOF",
    "source /dev/stdin <<EOF\n{c}\nEOF",
    ". /dev/stdin <<'EOF'\n{c}\nEOF",
    "\"$SHELL\" <<'EOF'\n{c}\nEOF",
    "${{SHELL}} <<EOF\n{c}\nEOF",
    "eval \"$(cat)\" <<'EOF'\n{c}\nEOF",
    "exec bash <<'EOF'\n{c}\nEOF",
    "xargs -0 sh -c <<'EOF'\n{c}\nEOF",
    "cat <<'EOF' | sudo -s\n{c}\nEOF",
    "cat <<'EOF' | su\n{c}\nEOF",
    "cat <<'EOF' | xargs sh -c\n{c}\nEOF",
    "some-tool <<'EOF'\n{c}\nEOF",
    "echo ${{x/<</y}}\n{c}",
    "bash -c \"$(cat <<'EOF'\n{c}\nEOF\n)\"",
    "eval \"$(cat <<'EOF'\n{c}\nEOF\n)\"",
    "sh -c \"$(cat <<EOF\n{c}\nEOF\n)\"",
    "bash -c \"$(\ncat <<'EOF'\n{c}\nEOF\n)\"",
    "eval `cat <<'EOF'\n{c}\nEOF\n`",
    "python3 -c \"$(cat <<'EOF'\n{c}\nEOF\n)\"",
    "git commit -m \"$(echo \"$(cat <<'EOF'\n{c}\nEOF\n)\")\"",
    "bash <(cat <<'EOF'\n{c}\nEOF\n)",
    "tee >(bash) <<'EOF'\n{c}\nEOF",
    "cat <<'EOF' > >(bash)\n{c}\nEOF",
    "cat > /tmp/s.sh <<'EOF'\n{c}\nEOF\nbash /tmp/s.sh",
    "cat > s.sh <<'EOF'\n{c}\nEOF\nchmod +x s.sh && ./s.sh",
    "tee /tmp/x.sh >/dev/null <<'EOF'\n{c}\nEOF\nsource /tmp/x.sh",
]
READER_ALLOWED = [
    "sudo tee /tmp/n.conf <<'EOF'\nit's {c}\nEOF",
    "cat <<'EOF' | grep it\nit's {c}\nEOF",
    "python3 - <<'EOF'\nprint(\"it's {c}\")\nEOF",
    "env FOO=1 cat > /tmp/n.txt 2>&1 <<'EOF'\nit's {c}\nEOF",
    "gh issue create --body-file - <<'EOF'\nit's {c}\nEOF",
    "git commit -m \"$(cat <<'EOF'\nwe don't {c}\nEOF\n)\"",
    "git commit -m \"$(date)\" -m \"$(cat <<'EOF'\nwe don't {c}\nEOF\n)\"",
    "gh pr create --title t --body \"$(cat <<'EOF'\nit's {c}\nEOF\n)\"",
    "x=$(cat <<'EOF'\nit's {c}\nEOF\n)",
    "export MSG=\"$(cat <<'EOF'\nit's {c}\nEOF\n)\"",
    "cat > /tmp/s.sh <<'EOF'\nit's {c}\nEOF\nbash /tmp/other.sh",
]


@pytest.mark.parametrize("shape", READER_SHAPES)
def test_heredoc_body_a_shell_may_read_is_checked(shape):
    cmd = shape.format(c="rm -f ~/.hermes/state.db")
    assert plugin.guard.check_shell(cmd) == RM_STATE, cmd
    assert blocked("terminal", command=cmd), cmd


@pytest.mark.parametrize("shape", READER_ALLOWED)
def test_heredoc_body_a_data_reader_reads_stays_allowed(shape):
    cmd = shape.format(c="rm -f ~/.hermes/state.db")
    assert plugin.guard.check_shell(cmd) is None, cmd
    assert not blocked("terminal", command=cmd), cmd


# Store data first, then run it in the same tool call. Test actual admission, not extraction.
STORED_ASSIGNMENT = "x=$(cat <<'EOF'\n{c}\nEOF\n)\n"
STORED_FILE = "cat > /tmp/stored-heredoc-regression.sh <<'EOF'\n{c}\nEOF\n"
STORED_RUNS = [
    pytest.param(STORED_ASSIGNMENT + 'eval "$x"', id="assignment-eval"),
    pytest.param(STORED_ASSIGNMENT + 'bash -c "$x"', id="assignment-bash-c"),
    pytest.param(STORED_ASSIGNMENT + '$x', id="assignment-command"),
    pytest.param(STORED_ASSIGNMENT + 'source <(printf "%s\\n" "$x")', id="assignment-source"),
    pytest.param(STORED_FILE + 'bash < /tmp/stored-heredoc-regression.sh', id="file-shell-stdin"),
    pytest.param(STORED_FILE + 'cat /tmp/stored-heredoc-regression.sh | bash', id="file-pipe-shell"),
    pytest.param(STORED_FILE + 'sh -c "$(cat /tmp/stored-heredoc-regression.sh)"', id="file-shell-cat"),
    pytest.param(STORED_FILE + 'eval "$(< /tmp/stored-heredoc-regression.sh)"', id="file-eval-read"),
]
STORED_CONTROLS = [
    pytest.param(STORED_ASSIGNMENT, id="assignment-no-run"),
    pytest.param('git commit -m "$(cat <<\'EOF\'\n{c}\nEOF\n)"', id="commit-message"),
    pytest.param(STORED_FILE + 'cat /tmp/stored-heredoc-regression.sh', id="write-then-cat"),
    pytest.param(STORED_ASSIGNMENT + "echo 'eval \"$x\"; bash -c \"$x\"; $x'", id="quoted-variable-references"),
    pytest.param(STORED_FILE + "echo 'bash < /tmp/stored-heredoc-regression.sh; cat /tmp/stored-heredoc-regression.sh | bash'", id="quoted-file-references"),
]


@pytest.mark.parametrize("shape", STORED_RUNS)
def test_stored_heredoc_execution_refused_at_hook(shape):
    command = shape.format(c="rm -f ~/.hermes/state.db")
    result = plugin._pre_tool_call(tool_name="terminal", args={"command": command})
    assert result and result["action"] == "block", (command, result)
    assert "sqlite-live-guard" in result["message"]


@pytest.mark.parametrize("shape", STORED_CONTROLS)
def test_stored_heredoc_data_allowed_at_hook(shape):
    command = shape.format(c="rm -f ~/.hermes/state.db")
    assert plugin._pre_tool_call(tool_name="terminal", args={"command": command}) is None, command


# Variables named like shell keywords: an active expansion stays visible, quoted text stays inert.
def _kw_assigned(name):
    return "%s=$(cat <<'EOF'\n{c}\nEOF\n)\n" % name


KEYWORD_NAME_RUNS = [
    pytest.param(_kw_assigned("if") + 'eval "$if"', id="eval-dollar-if"),
    pytest.param(_kw_assigned("if") + 'eval "${{if}}"', id="eval-brace-if"),
    pytest.param(_kw_assigned("do") + 'bash -c "$do"', id="bash-c-dollar-do"),
    pytest.param(_kw_assigned("then") + 'bash -c "${{then}}"', id="bash-c-brace-then"),
]
KEYWORD_NAME_CONTROLS = [
    pytest.param(STORED_ASSIGNMENT + 'echo "if $x"', id="echo-if-word"),
    pytest.param(STORED_ASSIGNMENT + 'echo "do $x; then $x"', id="echo-do-then-words"),
    pytest.param(_kw_assigned("if") + 'echo "if $if"', id="echo-expands-if-var"),
]


@pytest.mark.parametrize("shape", KEYWORD_NAME_RUNS)
def test_keyword_named_variable_run_refused_at_hook(shape):
    command = shape.format(c="rm -f ~/.hermes/state.db")
    result = plugin._pre_tool_call(tool_name="terminal", args={"command": command})
    assert result and result["action"] == "block", (command, result)
    assert "sqlite-live-guard" in result["message"]


@pytest.mark.parametrize("shape", KEYWORD_NAME_CONTROLS)
def test_keyword_words_in_quotes_stay_allowed_at_hook(shape):
    command = shape.format(c="rm -f ~/.hermes/state.db")
    assert plugin._pre_tool_call(tool_name="terminal", args={"command": command}) is None, command
