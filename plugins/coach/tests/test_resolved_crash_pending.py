"""Deferred storms: disposition time is not crash time; missing proof must file."""
import json
import sqlite3
import subprocess

import pytest
from test_self_heal import sh, core


@pytest.fixture
def incident(tmp_path, monkeypatch):
    store = core.Store(tmp_path / "coach", clock=lambda: 1794273684)
    repo = tmp_path / "core"
    repo.mkdir()
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()
    git("init", "-q")
    git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-qm", "fix storage failure")
    sha = git("rev-parse", "HEAD")
    monkeypatch.setattr(sh, "_fork_repo", lambda: repo)
    monkeypatch.setattr(sh, "host_name", lambda: "example-host")
    for name in ("_sqlite_incident_over", "kanban_trigger_closed", "_monitor_withdrawn"):
        monkeypatch.setattr(sh, name, lambda *a, **k: None)
    board = tmp_path / "board.db"
    monkeypatch.setattr(sh, "_kanban_board_path", lambda *a: board)
    with sqlite3.connect(board) as db:
        db.execute("CREATE TABLE tasks (id TEXT, body TEXT)")
        db.execute("CREATE TABLE task_comments (task_id TEXT, body TEXT)")
        db.execute("INSERT INTO tasks VALUES ('t_a', 'Host: example-host')")
        db.execute("INSERT INTO task_comments VALUES ('t_a', ?)", (
            "example-host: WorkerPersistenceError: storage_unavailable; crash window "
            "2026-11-04T00:26:34Z..2026-11-04T00:27:17Z",))
    sh.LaneState(store.root).update("agent-sibling", status="stale", card="t_a", board="default",
        symptom="WorkerPersistenceError in flush_token_counts: storage_unavailable")
    store.log({"event": "selfheal_duplicate", "fingerprint": "agent-sibling", "card": "t_a",
               "note": f"Same incident; latch fixed by {sha}."})
    d = {"source": "signal", "host": "example-host", "fingerprint": "kanban-pending",
         "symptom": "managed-worker crash storm: WorkerPersistenceError: storage_unavailable",
         "details": "Crash times (UTC, log headers): earliest 2026-11-03T23:17:47Z, latest 2026-11-04T00:17:58Z; core: old"}
    return store, d, board, sha


def retry(store, d):
    sh._defer_investigation(d, {"retry_at": "2026-11-10T00:00:00Z"}, store)
    filed = []
    def filer(d, **kw):
        filed.append(d)
        return {"action": "filed"}
    result = sh.retry_deferred(store, now=1794273684, filer=filer)
    with sh._pending_db(store) as db:
        assert db.execute("SELECT count(*) FROM pending").fetchone()[0] == 0
    return result, filed


@pytest.mark.parametrize("kind,partial,abbreviated", [("duplicate", False, False), ("fixed", True, True)])
def test_legacy_duplicate_stale_lane_skips_with_deployed_fix(incident, kind, partial, abbreviated):
    store, d, board, _ = incident
    if kind == "fixed":
        p = store.root / "log.jsonl"
        p.write_text(p.read_text().replace("selfheal_duplicate", "selfheal_fixed"))
    if partial:
        d["details"] = d["details"].replace("headers)", "headers) (2 of 7 pids stamped)")
    if abbreviated:
        with sqlite3.connect(board) as db:
            db.execute("UPDATE task_comments SET body=replace(body,'2026-11-04T00:27:17Z','00:27:17Z')")
    result, filed = retry(store, d)
    assert result == [{"action": "skip", "why": "resolved under agent-sibling"}]
    assert not filed
    rows = [json.loads(s) for s in (store.root / "log.jsonl").read_text().splitlines()]
    assert any(r.get("event") == "selfheal_skip" and r.get("why") == "resolved under agent-sibling" for r in rows)


@pytest.mark.parametrize("change", ["window", "error", "host", "remote", "no_sha", "unknown_sha", "no_window", "not_signal", "reverted", "refiled", "malformed_lane", "post_resolution"])
def test_insufficient_or_different_evidence_files(incident, change):
    store, d, board, sha = incident
    if change == "window":
        d["details"] = d["details"].replace("2026-11-03", "2026-11-08").replace("2026-11-04", "2026-11-09")
    elif change == "error":
        d["symptom"] = d["symptom"].replace("storage_unavailable", "permission_denied")
    elif change == "host":
        with sqlite3.connect(board) as db:
            db.execute("UPDATE task_comments SET body=replace(body,'example-host','other-host')")
    elif change == "remote":
        d["host"] = "other-host"
    elif change in ("no_sha", "unknown_sha"):
        p = store.root / "log.jsonl"
        p.write_text(p.read_text().replace(sha, "absent" if change == "no_sha" else "abcdef0123456789"))
    elif change == "malformed_lane":
        sh.LaneState(store.root).update("agent-sibling", board={"bad": True})
    elif change == "post_resolution":
        p = store.root / "log.jsonl"
        rows = [json.loads(s) for s in p.read_text().splitlines()]
        for row in rows:
            row["ts"] = 1
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    elif change == "refiled":
        store.log({"event": "selfheal_filed", "fingerprint": "agent-sibling"})
    elif change == "no_window":
        d["details"] = "discovered 2026-11-04T00:17:58Z"
    elif change == "not_signal":
        d["source"] = "complaint"
    else:
        store.log({"event": "selfheal_reverted", "fingerprint": "agent-sibling"})
    result, filed = retry(store, d)
    assert result == [{"action": "filed"}]
    assert len(filed) == 1
