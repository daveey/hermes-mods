"""Disk pressure owns concurrent SQLite failures, not unrelated hosts/events."""
import pytest
from test_self_heal_lane import sh, store, Runner, OPEN, HUB, defect, _real_adoption_env, _board

NOW = 1793743920.0  # 2026-11-03T22:12Z; source host sanitized below


def payload():
    d = defect(component="agent", source="signal", session_id="", quote="", host="test-host",
               symptom="OperationalError in open_db (sqlite_util.py): Cron job future failed in async mode: disk I/O error",
               details="cron/executions.db PRAGMA synchronous=FULL\nsqlite3.OperationalError: disk I/O error",
               at=NOW)
    d["fingerprint"] = "agent-2db8a02f14"
    return d


def test_disk_io_adopts_disk_owner(store, tmp_path, monkeypatch):
    _real_adoption_env(monkeypatch, tmp_path)
    _board(tmp_path, [("t_0011", "test-host disk critical: ENOSPC", "", "running", NOW - 300)])
    r = Runner()
    out = sh.file_defect(payload(), store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)
    assert out["action"] == "skip" and out.get("adopted") and out["card"] == "t_0011"
    assert not r.creates()
    assert any("comment" in a for a, _ in r.calls)


def test_truncated_signal_uses_local_journal(store, tmp_path, monkeypatch):
    from types import SimpleNamespace
    _real_adoption_env(monkeypatch, tmp_path)
    monkeypatch.setattr(sh, "host_name", lambda: "test-host")
    _board(tmp_path, [("t_0011", "test-host disk critical: ENOSPC", "", "running", NOW - 300)])
    calls = []
    def journal(argv, **kw):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="OSError: [Errno 28] No space left on device\n")
    monkeypatch.setattr("subprocess.run", journal)
    d = {**payload(), "symptom": "Exception in refresh_tick (turn_facade_lease.py): Failed to refresh session turn lease",
         "details": "managed-worker.log not written (OSError)"}
    r = Runner()
    out = sh.file_defect(d, store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)
    assert out.get("adopted") and out["card"] == "t_0011"
    assert not r.creates()
    assert calls[0][0] == "journalctl"
    assert calls[0][calls[0].index("--since") + 1] == f"@{NOW - 120:.0f}"
    assert calls[0][calls[0].index("--until") + 1] == f"@{NOW + 120:.0f}"


@pytest.mark.parametrize("title,status,delta,adopt", [
    ("test-host disk ENOSPC", "done", -7200, True),
    ("test-host disk 99% used", "archived", 7200, True),
    ("test-host disk critical", "done", -7201, False),
    ("test-host disk free 1GB", "done", 7201, False),
    ("test-host disk critical", "running", -86400, True),
    ("other-host disk critical", "running", 0, False),
    ("other-test-host disk critical", "running", 0, False),
    ("test-host database critical", "running", 0, False),
    ("test-host disk driver", "running", 0, False),
])
def test_host_and_event_boundaries(store, tmp_path, monkeypatch, title, status, delta, adopt):
    _real_adoption_env(monkeypatch, tmp_path)
    _board(tmp_path, [("t_0011", title, "", status, NOW + delta)])
    r = Runner()
    out = sh.file_defect(payload(), store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)
    assert bool(out.get("adopted")) == adopt
    assert len(r.creates()) == (0 if adopt else 1)


def test_no_disk_card_files_as_before(store, tmp_path, monkeypatch):
    _real_adoption_env(monkeypatch, tmp_path)
    _board(tmp_path, [])
    r = Runner()
    out = sh.file_defect(payload(), store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)
    assert out["action"] == "filed" and len(r.creates()) == 1


@pytest.mark.parametrize("face", ["disk I/O error", "database or disk is full", "[Errno 28]", "No space left on device"])
def test_faces_shadow_read_only(store, tmp_path, monkeypatch, face):
    _real_adoption_env(monkeypatch, tmp_path)
    _board(tmp_path, [("t_0011", "test-host disk critical", "", "running", NOW)])
    r = Runner()
    out = sh.file_defect({**payload(), "symptom": "cron execution failed", "details": face}, store=store, m="shadow", runner=r, now=NOW, cfg=HUB)
    assert out.get("adopted") and not r.calls
    assert not sh.LaneState(store.root).read()


def test_kanban_family_unchanged():
    assert sh.root_family({"details": "kanban.db sqlite3.OperationalError: disk I/O error"}) == "kanban-db-view"


@pytest.mark.parametrize("failure", ["empty", "denied", "timeout", "missing", "remote"])
def test_journal_unavailable_does_not_adopt(store, tmp_path, monkeypatch, failure):
    import subprocess
    from types import SimpleNamespace
    _real_adoption_env(monkeypatch, tmp_path)
    monkeypatch.setattr(sh, "host_name", lambda: "other-host" if failure == "remote" else "test-host")
    _board(tmp_path, [("t_0011", "test-host disk critical", "", "running", NOW)])
    def journal(*a, **kw):
        if failure == "remote":
            pytest.fail("must not read local journal for remote host")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(a[0], 5)
        if failure == "missing":
            raise FileNotFoundError("journalctl")
        return SimpleNamespace(returncode=1 if failure == "denied" else 0, stdout="")
    monkeypatch.setattr(subprocess, "run", journal)
    r = Runner()
    out = sh.file_defect({**payload(), "symptom": "refresh_tick failed", "details": "truncated traceback"}, store=store, m="live",
                         runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)
    assert out["action"] == "filed" and len(r.creates()) == 1


def test_comment_failure_not_reported_as_adoption(store, tmp_path, monkeypatch):
    _real_adoption_env(monkeypatch, tmp_path)
    _board(tmp_path, [("t_0011", "test-host disk critical", "", "running", NOW)])
    r = Runner(fail=True)
    out = sh.file_defect(payload(), store=store, m="live", runner=r, now=NOW, cfg=HUB)
    assert out["action"] == "error" and not out.get("adopted") and not r.creates()
