"""Tests for stranded_restart.py. Run: python3 -m pytest -q -p no:cacheprovider tests_stranded_restart.py

A real child process opens a WAL database, commits (frames in -wal), then the -wal is unlinked under it, which is
the state the hub dashboard was in at 04:13Z on 2026-10-28 (498 KB of frames in a deleted kanban.db-wal).
"""

import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("stranded_restart", HERE / "stranded_restart.py")
sr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sr)

CHILD = textwrap.dedent("""
    import sqlite3, sys, time
    db, write = sys.argv[1], sys.argv[2] == "1"
    c = sqlite3.connect(db, isolation_level=None)
    c.execute("pragma journal_mode=wal"); c.execute("pragma wal_autocheckpoint=0")
    c.execute("create table if not exists t (x)")
    c.execute("pragma wal_checkpoint(truncate)")
    print("open", flush=True)
    sys.stdin.readline()  # parent unlinks the sidecars now
    if write:
        c.execute("insert into t values (1)")
    print("done", flush=True)
    time.sleep(30)
""")


def _child(tmp_path, write):
    home = tmp_path / "home"
    home.mkdir()
    db = home / "kanban.db"
    p = subprocess.Popen([sys.executable, "-c", CHILD, str(db), "1" if write else "0"],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "open"
    for s in ("-wal", "-shm"):
        Path(str(db) + s).unlink()
    p.stdin.write("go\n")
    p.stdin.flush()
    assert p.stdout.readline().strip() == "done"
    return p, home


class FakeRun:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")


def test_restarts_unit_whose_deleted_wal_has_frames(tmp_path):
    p, home = _child(tmp_path, write=True)
    try:
        fake = FakeRun()
        res = sr.run(["dash.service"], str(home), runner=fake, pids_for=lambda u: [p.pid])
        entry = res["units"]["dash.service"]
        assert entry["dirty"] and entry["dirty"][0]["bytes"] > 0
        assert entry["restarted"] and ["systemctl", "--user", "restart", "dash.service"] in fake.calls
        assert entry["archived"] and Path(entry["archived"][0]).stat().st_size == entry["dirty"][0]["bytes"]
    finally:
        p.kill()
        p.wait()


def test_reader_with_empty_deleted_wal_is_left_alone(tmp_path):
    p, home = _child(tmp_path, write=False)
    try:
        fake = FakeRun()
        res = sr.run(["dash.service"], str(home), runner=fake, pids_for=lambda u: [p.pid])
        assert res["units"]["dash.service"] == {"pids": 1, "dirty": [], "restarted": False, "archived": []}
        assert fake.calls == []
    finally:
        p.kill()
        p.wait()


def test_dry_run_reports_without_restarting(tmp_path):
    p, home = _child(tmp_path, write=True)
    try:
        fake = FakeRun()
        res = sr.run(["dash.service"], str(home), dry_run=True, runner=fake, pids_for=lambda u: [p.pid])
        assert res["units"]["dash.service"]["dirty"] and fake.calls == []
    finally:
        p.kill()
        p.wait()
