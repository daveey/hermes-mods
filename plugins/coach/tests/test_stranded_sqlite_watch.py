"""stranded_sqlite_watch: finds a real process holding deleted kanban.db -wal/-shm under HERMES_HOME; --dry-run
files nothing; a real run files one card per finding with a stable idempotency key."""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("stranded_sqlite_watch_test", ROOT / "stranded_sqlite_watch.py")
sw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sw)

pytestmark = pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="Linux /proc only")

CHILD = """
import sqlite3, sys, time
db = sqlite3.connect(sys.argv[1])
db.execute("select count(*) from t").fetchone()   # opens -wal/-shm and keeps them open
db.execute("begin"); db.execute("select * from t").fetchall()
print("ready", flush=True)
time.sleep(60)
"""


@pytest.fixture
def stranded(tmp_path):
    """A fake HERMES_HOME with a WAL board db; a child holds it open; its -wal/-shm are then unlinked under it."""
    home = tmp_path / "home"
    path = home / "kanban" / "boards" / "proj" / "kanban.db"
    path.parent.mkdir(parents=True)
    db = sqlite3.connect(path)
    db.execute("pragma journal_mode=wal")
    db.execute("create table t(x)")
    db.execute("insert into t values(1)")
    db.commit()
    db.close()
    child = subprocess.Popen([sys.executable, "-c", CHILD, str(path)], stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        for s in ("-wal", "-shm"):
            assert Path(f"{path}{s}").exists()
            os.unlink(f"{path}{s}")
        yield SimpleNamespace(home=home, db=str(path.resolve()), pid=child.pid)
    finally:
        child.kill()
        child.wait()


class Runner:
    def __init__(self):
        self.calls, self.keys = [], {}

    def __call__(self, argv, input=None, **kw):
        self.calls.append((argv, input))
        key = argv[argv.index("--idempotency-key") + 1]
        tid = self.keys.setdefault(key, f"t_{len(self.keys):08x}")
        return SimpleNamespace(returncode=0, stdout=json.dumps({"id": tid}), stderr="")


def test_scan_finds_the_child_holding_deleted_sidecars(stranded):
    got = [f for f in sw.scan(str(stranded.home)) if f["pid"] == stranded.pid]
    assert len(got) == 1
    f = got[0]
    assert f["db"] == stranded.db and f["starttime"]
    assert sorted(Path(s["path"]).name for s in f["sidecars"]) == ["kanban.db-shm", "kanban.db-wal"]
    assert all(s["deleted_ctime"] and s["deleted_ctime"] <= time.time() + 1 for s in f["sidecars"])
    assert "sqlite3" in f["cmdline"]


def test_scan_ignores_other_homes(stranded, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert not [f for f in sw.scan(str(other)) if f["pid"] == stranded.pid]


def test_dry_run_prints_findings_and_creates_nothing(stranded, capsys):
    r = Runner()
    assert sw.main(["--dry-run", "--home", str(stranded.home)], runner=r) == 0
    out = json.loads(capsys.readouterr().out)
    assert any(f["pid"] == stranded.pid for f in out["findings"]) and out["cards"] == []
    assert r.calls == []


def test_real_run_files_one_keyed_card_and_repeat_reuses_the_key(stranded, capsys):
    r = Runner()
    assert sw.main(["--json", "--home", str(stranded.home)], runner=r) == 0
    first = json.loads(capsys.readouterr().out)
    mine = [c for c in first["cards"] if f":{stranded.pid}:" in c["key"]]
    assert len(mine) == 1
    argv, body = next(c for c in r.calls if f":{stranded.pid}:" in c[0][c[0].index("--idempotency-key") + 1])
    assert argv[:3] == ["hermes", "kanban", "create"] and argv[argv.index("--assignee") + 1] == "default"
    assert argv[-1] == f"Process {stranded.pid} stranded on deleted kanban.db WAL/SHM"
    assert "kanban_lockguard.py" in body and "Do NOT touch the live db" in body and stranded.db in body
    assert sw.main(["--json", "--home", str(stranded.home)], runner=r) == 0
    second = json.loads(capsys.readouterr().out)
    assert [c["key"] for c in second["cards"]] == [c["key"] for c in first["cards"]]  # same key -> same card
    assert len(r.keys) == len(first["cards"])


def test_no_findings_creates_nothing(tmp_path, capsys):
    r = Runner()
    (tmp_path / "h").mkdir()
    assert sw.main(["--home", str(tmp_path / "h")], runner=r) == 0
    assert r.calls == [] and "0 stranded" in capsys.readouterr().out
