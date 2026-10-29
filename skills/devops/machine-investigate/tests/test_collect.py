"""Tests for machine-investigate collect.py: python3 -m pytest skills/devops/machine-investigate/tests"""
import importlib.util
import json
import sqlite3
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/collect.py"


@pytest.fixture
def c(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("collect", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "HOME", tmp_path)
    return mod


PS = """\
    1     0   0.0  1000 13-00:00:00 /sbin/launchd
  200     1   1.0 40960 01:00:00 /Users/u/Applications/Hermes Python.app/Contents/MacOS/python3.14 -I -c import sys, runpy; sys.argv = ['-c', 'gateway', 'run']
  210   200   2.0 40960 00:20:00 /Users/u/Applications/Hermes Python.app/Contents/MacOS/python3.14 -m hermes_cli.main -p bb-dev chat
  211   210  55.0 81920 00:19:00 /usr/bin/python3 -m pytest tests/foo
  300     1  40.0 40960 13-00:00:00 /System/Library/PrivateFrameworks/SkyLight.framework/Resources/WindowServer -daemon
  400     1  25.0 40960 02:40:00 /Users/u/Applications/BB Pod.app/Contents/MacOS/BBPod
  500     1  30.0 40960 00:05:00 /opt/homebrew/bin/node /tmp/leftover.js
  600     1   9.0  4096 00:01 /opt/homebrew/bin/gh api repos/x/y/commits/main
  601     1   8.0  4096 00:02 /opt/homebrew/bin/gh api repos/x/y/pulls
"""
TAGS = {
    210: {"HERMES_KANBAN_TASK": "t_aaaa1111", "HERMES_SESSION_ID": "s1"},
    400: {"HERMES_KANBAN_TASK": "t_dead0001"},  # GUI app launched from a worker long ago
    500: {"HERMES_KANBAN_TASK": "t_done0001"},  # orphan of a finished card
}


def test_processes_attribute_owner_through_the_parent_chain(c):
    t = c.parse_ps(PS)
    out = c.processes(t, tags_fn=lambda pids: {p: v for p, v in TAGS.items() if p in pids})
    top = {r["pid"]: r for r in out["top"]}
    assert top[211]["owner"] == "card:t_aaaa1111"
    assert top[211]["chain"] == ["hermes chat[210]", "hermes gateway run[200]"]
    assert top[211]["root"] == "hermes gateway run"
    assert top[300]["owner"] == "" and top[300]["name"] == "WindowServer"
    assert top[400]["name"] == "BB Pod" and top[400]["owner"] == "card:t_dead0001"  # orphaned directly under launchd
    assert dict(out["young_by_name"])["gh"] == 2


def test_kill_only_a_finished_cards_leftover(c):
    t = c.parse_ps(PS)
    status = {"t_done0001": "done", "t_aaaa1111": "running", "t_dead0001": "archived"}.get
    ok, why = c.kill_verdict(t, TAGS, 500, status_fn=status)
    assert ok and "t_done0001 is done" in why
    ok, why = c.kill_verdict(t, TAGS, 211, status_fn=status)
    assert not ok and "running" in why
    ok, why = c.kill_verdict(t, TAGS, 400, status_fn=status)
    assert not ok and "GUI app" in why
    ok, why = c.kill_verdict(t, TAGS, 200, status_fn=status)
    assert not ok and "gateway" in why
    ok, why = c.kill_verdict(t, TAGS, 300, status_fn=status)
    assert not ok and "no Kanban card" in why
    assert c.kill_verdict(t, TAGS, 1, status_fn=status)[0] is False
    # A Hermes worker (python inside Hermes Python.app) still alive after its card finished is a leftover.
    assert c.kill_verdict(t, TAGS, 210, status_fn={"t_aaaa1111": "blocked"}.get)[0] is True


def test_card_status_reads_board_replicas(c, tmp_path):
    (tmp_path / "kb" / "sync-shadow").mkdir(parents=True)
    db = sqlite3.connect(tmp_path / "kb/sync-shadow/default.db")
    db.execute("CREATE TABLE tasks (id TEXT, status TEXT)")
    db.execute("INSERT INTO tasks VALUES ('t_done0001', 'done')")
    db.commit()
    assert c.card_status("t_done0001", [tmp_path / "kb"]) == "done"
    assert c.card_status("t_missing01", [tmp_path / "kb"]) is None
    assert c.card_status("'; drop", [tmp_path / "kb"]) is None


def _fill(path: Path, mb: int):
    path.mkdir(parents=True, exist_ok=True)
    (path / "blob").write_bytes(b"x" * mb * 1024 * 1024)


def test_clean_touches_only_allowlisted_caches(c, tmp_path, monkeypatch):
    monkeypatch.setattr(c.shutil, "which", lambda name: None)  # no tools: plain removal path
    _fill(tmp_path / ".cache/uv", 60)
    _fill(tmp_path / ".npm/_npx", 60)
    _fill(tmp_path / ".cache/huggingface", 60)   # model downloads: not allowlisted
    _fill(tmp_path / "code/repo", 60)
    _fill(tmp_path / ".cache/pip", 1)            # under 50 MB: left alone
    dry = c.clean(apply=False)
    assert {a["name"] for a in dry["actions"]} == {"uv cache", "npx cache"}
    assert (tmp_path / ".cache/uv/blob").exists()
    done = c.clean(apply=True)
    assert all(a.get("removed") for a in done["actions"] if a["name"] in ("uv cache", "npx cache"))
    assert not (tmp_path / ".cache/uv").exists() and not (tmp_path / ".npm/_npx").exists()
    assert (tmp_path / ".cache/huggingface/blob").exists() and (tmp_path / "code/repo/blob").exists()
    assert (tmp_path / ".cache/pip/blob").exists()
    assert not c._safe_target(tmp_path) and not c._safe_target(tmp_path / "code")


def test_spikes_summarise_the_recorder_files(c, tmp_path):
    now = time.time()
    (tmp_path / "load-2026-10-29.tsv").write_text(
        "".join(f"{int(now) - 60 * i}\t{l}\t1\t1\t8\n" for i, l in enumerate([5, 12, 30, 9])))
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "by_agent": [["card:t_x", 80.0], ["app:WindowServer", 40.0]]}
    (tmp_path / "spikes-2026-10-29.jsonl").write_text(json.dumps(rec) + "\n")
    out = c.spikes(24, tmp_path)
    assert out["samples"] == 4 and out["load1"]["max"] == 30 and out["spike_records"] == 1
    assert out["mean_cpu_by_agent_in_spikes"][0] == ["card:t_x", 80.0]


def test_recorder_threshold_scales_with_cores(c):
    assert "<string>20</string>" in c.recorder_plist(8)
    assert "<string>40</string>" in c.recorder_plist(16)


def test_report_runs_on_this_machine(c):
    out = c.report(1, with_du=False)
    assert out["cpus"] >= 1 and isinstance(out["processes"]["top"], list)
    assert out["disk"]["home_volume"]["total_gb"] > 0


def test_clean_survives_a_hanging_tool_and_still_removes_the_cache(c, tmp_path, monkeypatch):
    import subprocess as sp
    _fill(tmp_path / ".cache/uv", 60)
    monkeypatch.setattr(c.shutil, "which", lambda name: "/bin/" + name)
    real = sp.run
    def run(cmd, *a, **kw):
        if cmd[0] == "uv":
            raise sp.TimeoutExpired(cmd, kw.get("timeout"))
        return real(cmd, *a, **kw)
    monkeypatch.setattr(c.subprocess, "run", run)
    done = c.clean(apply=True)
    uv = next(a for a in done["actions"] if a["name"] == "uv cache")
    assert uv["exit"] == "timeout after 180 s" and uv["removed"] and not (tmp_path / ".cache/uv").exists()


def test_report_no_du_never_walks_caches(c, monkeypatch, tmp_path):
    """--no-du must not run du at all (it hung >180 s at load 70 on multi-GB caches)."""
    (tmp_path / ".cache/uv").mkdir(parents=True)
    calls = []
    monkeypatch.setattr(c, "du_kb", lambda *a, **k: calls.append(a) or 1)
    d = c.disk(with_du=False)
    assert calls == []
    assert d["cleanable"] and d["cleanable"][0]["gb"] is None
    assert "largest_in_home" not in d


def test_card_status_does_not_walk_deep_trees(c, tmp_path):
    """workspaces/ and evidence/ hold huge trees (and build.db files); only board DBs at depth <= 2 are read."""
    root = tmp_path / "kb"
    deep = root / "workspaces/t_x/.build/out"
    deep.mkdir(parents=True)
    con = sqlite3.connect(deep / "build.db")
    con.execute("CREATE TABLE tasks (id TEXT, status TEXT)")
    con.execute("INSERT INTO tasks VALUES ('t_deep0001', 'done')")
    con.commit()
    con.close()
    (root / "boards/b").mkdir(parents=True)
    con = sqlite3.connect(root / "boards/b/kanban.db")
    con.execute("CREATE TABLE tasks (id TEXT, status TEXT)")
    con.execute("INSERT INTO tasks VALUES ('t_board001', 'blocked')")
    con.commit()
    con.close()
    assert c.card_status("t_deep0001", [root]) is None
    assert c.card_status("t_board001", [root]) == "blocked"


THREAD_PS = """\
USER               PID   TT   %CPU STAT PRI     STIME     UTIME COMMAND
worker             211   ??    0.0 R+    4T   0:00.00   0:00.00 /usr/bin/python3 job with spaces
                   211         0.0 R     4N   0:00.00   0:00.00
                   211         0.0 S     4T   0:00.00   0:00.00
  worker           600   ??    0.0 R    31R   0:00.00   0:00.00 /bin/other
                   600         0.0 R     4    0:00.00   0:00.00
root               999   ??    0.0 R     4T   0:00.00   0:00.00 gone
malformed
                   xxx         0.0 R     4T
                   211         0.0 R    bad
"""


def test_runnable_parser_counts_threads_and_priority_flags(c):
    out = c.parse_runnable(THREAD_PS)
    assert out == {"total": 5, "by_priority": {"4": 4, "31": 1},
                   "priority4": [{"pid": 211, "threads": 2}, {"pid": 600, "threads": 1},
                                 {"pid": 999, "threads": 1}]}
    assert c.parse_runnable("")["total"] == 0
    many = "\n".join(f"user {p} ?? 0.0 R 4T 0:00.00 0:00.00 x" for p in range(30, 10, -1))
    assert [r["pid"] for r in c.parse_runnable(many)["priority4"]] == list(range(11, 21))


def test_runnable_attribution_includes_low_cpu_and_vanished_pids(c):
    t = c.parse_ps(PS)
    t[211] = dict(t[211], cpu=0.0)
    tags = {210: {"HERMES_KANBAN_TASK": "t_queue"}}
    out = c.attribute_runnable(c.parse_runnable(THREAD_PS), t, tags)
    assert out["priority4"][0]["owner"] == "card:t_queue"
    assert out["priority4"][0]["chain"]
    assert out["priority4"][-1]["name"] == "?"
    assert out["priority4"][-1]["owner"] == ""


def test_runnable_command_and_failure_are_explicit(c, monkeypatch):
    import subprocess
    calls = []
    def run(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, THREAD_PS, "")
    monkeypatch.setattr(c.subprocess, "run", run)
    assert c.runnable_snapshot()["total"] == 5
    assert calls[0][0] == ["/bin/ps", "-M", "-axc"]
    assert calls[0][1]["timeout"] == 2
    def fail(*a, **kw):
        raise subprocess.TimeoutExpired(a[0], 2)
    monkeypatch.setattr(c.subprocess, "run", fail)
    assert c.runnable_snapshot()["total"] is None
    assert c.runnable_snapshot()["error"] == "TimeoutExpired"


def test_report_wires_runnable_with_ancestry(c, monkeypatch):
    monkeypatch.setattr(c, "MAC", True)
    monkeypatch.setattr(c, "ps_table", lambda: c.parse_ps(PS))
    monkeypatch.setattr(c, "runnable_snapshot", lambda: c.parse_runnable(THREAD_PS))
    monkeypatch.setattr(c, "env_tags", lambda pids: TAGS)
    out = c.report(1, with_du=False)
    assert out["runnable"]["total"] == 5
    assert out["runnable"]["priority4"][0]["owner"] == "card:t_aaaa1111"


def test_report_linux_marks_thread_snapshot_unsupported(c, monkeypatch):
    monkeypatch.setattr(c, "MAC", False)
    monkeypatch.setattr(c, "runnable_snapshot", lambda: pytest.fail("Darwin ps must not run on Linux"))
    assert c.report(1, with_du=False)["runnable"]["supported"] is False


def test_runnable_numeric_username(c):
    out = c.parse_runnable("123456 211 ?? 0.0 R 4T 0:00.00 0:00.00 x\n  211 0.0 R 4T 0:00.00 0:00.00\n")
    assert out["total"] == 2
    assert out["priority4"] == [{"pid": 211, "threads": 2}]
