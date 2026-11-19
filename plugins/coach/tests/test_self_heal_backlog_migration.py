"""Digest → pending backlog migration: in-place keys, dry-run isolation, prune/resolved gates."""
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sh = load("coach_self_heal_backlog_migration_test", ROOT / "self_heal.py")
mig = load("coach_backlog_migration_test", ROOT / "backlog_migration.py")

NOW = 1795123200.0  # 2026-11-19 21:20 UTC
FP = "agent-0123456789"


def store(tmp_path):
    root = tmp_path / "coach"
    root.mkdir()
    events = []
    return SimpleNamespace(root=root, log=events.append, events=events)


def detection(**kw):
    d = {"fingerprint": FP, "component": "agent", "source": "signal", "host": "hermes-hub",
         "session_id": "20261119_000000_abc123", "profile": "default", "symptom": "broken reply",
         "quote": "the agent asked a question", "p_defect": 0.9, "at": NOW - 60}
    d.update(kw)
    return d


def comment_body(d, n=40, cap=5):
    return f"Over the daily cap ({n} >= {cap}), not filed (defect):\n" + sh._occurrence_text(d, NOW)


def inventory(comments, key="selfheal-digest:2026-11-19", status="done", card="t_digest1"):
    return {"cards": [{"id": card, "key": key, "status": status, "comments": comments}]}


def comment(cid, d=None, body=None):
    return {"id": cid, "body": body if body is not None else comment_body(d or detection()),
            "created_at": int(NOW)}


def pending_rows(st):
    path = st.root / "selfheal_pending.sqlite"
    if not path.exists():
        return {}
    db = sqlite3.connect(path)
    try:
        return {fp: json.loads(p) for fp, p in db.execute("SELECT fp, payload FROM pending")}
    finally:
        db.close()


@pytest.mark.parametrize("kind", ["receipt", "backport", "undeployed"])
def test_other_kinds_are_not_reclassified_as_defects(tmp_path, kind):
    st = store(tmp_path)
    body = comment_body(detection()).replace("not filed (defect)", f"not filed ({kind})")
    inv = inventory([comment(1, body=body)])
    out = mig.migrate_digest_backlog(sh, st, inventory=inv, prunes={"rows": []},
                                    dry_run=False, now=NOW)
    assert not pending_rows(st)
    assert out["other_kinds"] == [FP]
    assert mig.parse_digest_comment_blocks(sh, body)[0]["kind"] == kind
    again = mig.migrate_digest_backlog(sh, st, inventory=inv, prunes={"rows": []},
                                      dry_run=False, now=NOW)
    assert again["imported_comment_ids"] == []


def test_backup_failure_never_publishes_partial_bak(tmp_path, monkeypatch):
    src, dest = tmp_path / "source.sqlite", tmp_path / "source.sqlite.bak"
    with sh.contextlib.closing(sqlite3.connect(src)) as db:
        db.execute("CREATE TABLE example (value)")
        db.commit()
    original = mig.sqlite3.connect
    class BrokenSource:
        def backup(self, target):
            target.execute("CREATE TABLE incomplete (value)")
            target.commit()
            raise OSError("interrupted backup")
        def close(self):
            pass
    monkeypatch.setattr(mig.sqlite3, "connect", lambda p: BrokenSource() if p == src else original(p))
    with pytest.raises(OSError, match="interrupted backup"):
        mig._sqlite_backup(src, dest)
    assert not dest.exists()


def test_missing_provenance_fails_closed(tmp_path):
    st = store(tmp_path)
    inv = inventory([comment(1)])
    with pytest.raises(mig.DigestMigrationError, match="prune provenance"):
        mig.migrate_digest_backlog(sh, st, inventory=inv, prunes=None, dry_run=True, now=NOW)
    with pytest.raises(mig.DigestMigrationError, match="digest inventory"):
        mig.migrate_digest_backlog(sh, st, inventory=None, prunes={"rows": []}, dry_run=True, now=NOW)
    missing = tmp_path / "no-prunes.json"
    with pytest.raises(mig.DigestMigrationError, match="prune provenance"):
        mig.migrate_digest_backlog(sh, st, inventory=inv, prunes=missing, dry_run=True, now=NOW)


def test_comment_without_id_fails_closed(tmp_path):
    st = store(tmp_path)
    inv = inventory([{"body": comment_body(detection()), "created_at": int(NOW)}])
    with pytest.raises(mig.DigestMigrationError, match="missing id"):
        mig.migrate_digest_backlog(sh, st, inventory=inv, prunes={"rows": []}, dry_run=True, now=NOW)


def test_parse_occurrence_layout_nulls_and_p(tmp_path):
    st = store(tmp_path)
    d = detection(component=None, source=None, host=None, session_id="", symptom="", quote="")
    body = comment_body(d)
    assert "- fingerprint: " in body
    assert " · component ?" in body
    out = mig.migrate_digest_backlog(
        sh, st, inventory=inventory([comment(7, body=body)]), prunes={"rows": []},
        dry_run=False, now=NOW)
    row = pending_rows(st)[FP]
    assert row["p_defect"] == 0.5
    assert row["component"] is None
    assert row["source"] is None
    assert row["host"] is None
    assert row["session_id"] is None
    assert row["symptom"] is None
    assert out["queued"] == [FP]


def test_dryrun_does_not_touch_pending_db(tmp_path, monkeypatch):
    st = store(tmp_path)
    def boom(*a, **k):
        raise AssertionError("_pending_db must not run during dry-run")
    monkeypatch.setattr(sh, "_pending_db", boom)
    out = mig.migrate_digest_backlog(
        sh, st, inventory=inventory([comment(1), {"id": 2, "body": "SCHEDULED: until midnight",
                                                 "created_at": int(NOW)}]),
        prunes={"rows": []}, dry_run=True, now=NOW)
    assert out["dry_run"] is True
    assert out["queued"] == [FP]
    assert out["skipped_comments"] == 1
    assert not (st.root / "selfheal_pending.sqlite").exists()
    assert not list(st.root.glob("*.bak"))
    assert not (st.root / "selfheal_lane.json").exists()


def test_closed_and_generation_keys_imported_window_enforced(tmp_path):
    st = store(tmp_path)
    other = detection(fingerprint="desktop-aaaaaaaaaa", symptom="other")
    cards = {"cards": [
        {"id": "t_old", "key": "selfheal-digest:2026-11-12", "status": "done",
         "comments": [comment(1, other)]},
        {"id": "t_closed", "key": "selfheal-digest:2026-11-13", "status": "done",
         "comments": [comment(2)]},
        {"id": "t_gen", "key": "selfheal-digest:2026-11-19:2", "status": "archived",
         "comments": [comment(3, detection(fingerprint="cron-bbbbbbbbbb", symptom="cron fail"))]},
        {"id": "t_badgen", "key": "selfheal-digest:2026-11-19:x", "status": "done",
         "comments": [comment(4, other)]},
    ]}
    out = mig.migrate_digest_backlog(
        sh, st, inventory=cards, prunes={"rows": []}, dry_run=False, now=NOW)
    rows = pending_rows(st)
    assert FP in rows
    assert "cron-bbbbbbbbbb" in rows
    assert "desktop-aaaaaaaaaa" not in rows
    assert out["cards"] == 2


def test_prunes_raw_and_canonical(tmp_path, monkeypatch):
    st = store(tmp_path)
    raw, canon = "agent-rawrawraw1", "agent-canoncanon1"

    def canonize(d):
        fp = d.get("fingerprint")
        if fp == raw:
            return {**d, "fingerprint": canon, "raw_fingerprint": raw, "canon_fingerprint": canon}
        if fp == canon:
            return {**d, "fingerprint": canon, "raw_fingerprint": raw, "canon_fingerprint": canon}
        return d

    monkeypatch.setattr(sh, "canonicalize", canonize)
    live = detection(fingerprint="plugin-cccccccccc", symptom="still live")
    inv = inventory([
        comment(1, detection(fingerprint=raw, symptom="pruned raw")),
        comment(2, detection(fingerprint=canon, symptom="pruned canon")),
        comment(3, live),
    ])
    out = mig.migrate_digest_backlog(
        sh, st, inventory=inv, prunes={"rows": [{"fingerprint": raw}]}, dry_run=False, now=NOW)
    rows = pending_rows(st)
    assert raw not in rows and canon not in rows
    assert "plugin-cccccccccc" in rows
    assert set(out["pruned"]) == {raw, canon}


def test_resolved_not_queued_history_on_apply(tmp_path):
    st = store(tmp_path)
    sh.LaneState(st.root).update(FP, status="fixed", card="t_owner")
    out = mig.migrate_digest_backlog(
        sh, st, inventory=inventory([comment(9)]), prunes={"rows": []}, dry_run=True, now=NOW)
    assert FP in out["history_retained"]
    assert FP not in out["queued"]
    assert not pending_rows(st)
    assert sh.LaneState(st.root).get(FP)["status"] == "fixed"
    assert "backlog_evidence" not in sh.LaneState(st.root).get(FP)

    applied = mig.migrate_digest_backlog(
        sh, st, inventory=inventory([comment(9)]), prunes={"rows": []}, dry_run=False, now=NOW)
    assert FP in applied["history_retained"]
    assert FP not in pending_rows(st)
    stt = sh.LaneState(st.root).get(FP)
    assert stt["status"] == "fixed"
    assert stt["backlog_evidence"]["fingerprint"] == FP


def test_existing_pending_keys_preserved_and_folded(tmp_path):
    st = store(tmp_path)
    original = detection(at=NOW - 120, quote="first")
    db = sh._pending_db(st)
    try:
        with db:
            db.execute("INSERT INTO pending VALUES (?, ?, ?, ?)",
                       (FP, json.dumps(original), NOW - 10, "v0"))
    finally:
        db.close()
    newer = detection(at=NOW - 30, quote="second occurrence", session_id="later-session")
    out = mig.migrate_digest_backlog(
        sh, st, inventory=inventory([comment(4, newer)]), prunes={"rows": []},
        dry_run=False, now=NOW)
    rows = pending_rows(st)
    assert list(rows) == [FP]
    assert out["preserved_pending_keys"] == [FP]
    assert out["folded"] == [FP]
    assert rows[FP]["occurrence_count"] == 2
    assert (st.root / "selfheal_pending.sqlite.bak").exists()


def test_apply_provenance_idempotent(tmp_path):
    st = store(tmp_path)
    inv = inventory([comment(11), comment(12, detection(fingerprint="kanban-dddddddddd",
                                                       symptom="queue stall"))])
    first = mig.migrate_digest_backlog(
        sh, st, inventory=inv, prunes={"rows": []}, dry_run=False, now=NOW)
    keys = set(pending_rows(st))
    db = sqlite3.connect(st.root / "selfheal_pending.sqlite")
    try:
        n = db.execute(f"SELECT count(*) FROM {mig.PROVENANCE_TABLE}").fetchone()[0]
        kinds = {r[0] for r in db.execute(f"SELECT source_kind FROM {mig.PROVENANCE_TABLE}")}
    finally:
        db.close()
    assert n == 2
    assert kinds == {"block"}
    second = mig.migrate_digest_backlog(
        sh, st, inventory=inv, prunes={"rows": []}, dry_run=False, now=NOW)
    assert set(pending_rows(st)) == keys
    assert second["queued"] == [] and second["folded"] == []
    assert first["queued"]


@pytest.mark.parametrize("which", ["pending", "lane"])
def test_unreadable_state_fails_closed(tmp_path, which):
    st = store(tmp_path)
    if which == "lane":
        (st.root / "selfheal_lane.json").write_text("broken json")
    else:
        db = sh._pending_db(st)
        with db:
            db.execute("INSERT INTO pending VALUES (?, ?, ?, ?)", (FP, "broken json", NOW, "v"))
        db.close()
    with pytest.raises(mig.DigestMigrationError, match="pending payload|lane lifecycle"):
        mig.migrate_digest_backlog(sh, st, inventory=inventory([comment(1)]), prunes={"rows": []}, dry_run=False)
    assert not (st.root / "selfheal_pending.sqlite.bak").exists()


def test_prune_count_and_provenance_fail_closed(tmp_path):
    st = store(tmp_path)
    for prunes in ({"rows": []}, {"rows": [{"fingerprint": FP}]}):
        with pytest.raises(mig.DigestMigrationError, match="manual-prune provenance"):
            mig.migrate_digest_backlog(sh, st, inventory=inventory([comment(1)]),
                                      prunes=prunes, expected_prune_count=1)
    assert not list(st.root.iterdir())


def test_cli_defaults_readonly_then_explicit_apply(tmp_path, monkeypatch, capsys):
    st = store(tmp_path)
    inv = tmp_path / "inventory.json"
    prunes = tmp_path / "prunes.json"
    inv.write_text(json.dumps(inventory([comment(1)])))
    prunes.write_text(json.dumps({"rows": []}))
    monkeypatch.setattr(sh, "is_hub", lambda: True)
    monkeypatch.setattr(sh, "_store", lambda _: st)
    args = ["backlog-migrate", "--inventory", str(inv), "--prunes", str(prunes), "--expected-prunes", "0"]
    assert sh.main(args) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    assert not pending_rows(st)
    assert sh.main(args + ["--apply"]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is False
    assert FP in pending_rows(st)
    monkeypatch.setattr(sh, "is_hub", lambda: False)
    assert sh.main(args + ["--apply"]) == 2


def test_concurrent_pending_change_fails_closed(tmp_path, monkeypatch):
    import contextlib
    st = store(tmp_path)
    real = sh._admission_lock

    @contextlib.contextmanager
    def racing(*a, **kw):
        db = sh._pending_db(st)
        try:
            with db:
                db.execute("INSERT INTO pending VALUES (?, ?, ?, ?)",
                           (FP, json.dumps(detection(quote="concurrent")), NOW, "new"))
        finally:
            db.close()
        with real(*a, **kw):
            yield

    monkeypatch.setattr(sh, "_admission_lock", racing)
    with pytest.raises(mig.DigestMigrationError, match="snapshot changed"):
        mig.migrate_digest_backlog(sh, st, inventory=inventory([comment(1)]),
                                  prunes={"rows": []}, dry_run=False, now=NOW)
    assert pending_rows(st)[FP]["quote"] == "concurrent"
    assert mig._read_provenance(sh, st) == set()


def test_history_failure_replays_without_loss_or_double_count(tmp_path, monkeypatch):
    st = store(tmp_path)
    sh.LaneState(st.root).update(FP, status="fixed", card="t_owner")
    inv = inventory([comment(i, detection(at=NOW-i, quote=f"observation {i}")) for i in range(45)])
    real = sh.LaneState._apply
    calls = []

    def fail_once(lane, fp, kw):
        result = real(lane, fp, kw)
        calls.append(fp)
        if len(calls) == 30:
            raise OSError("simulated interrupted history write")
        return result

    monkeypatch.setattr(sh.LaneState, "_apply", fail_once)
    with pytest.raises(OSError, match="interrupted"):
        mig.migrate_digest_backlog(sh, st, inventory=inv, prunes={"rows": []}, dry_run=False, now=NOW)
    assert not mig._read_provenance(sh, st)
    monkeypatch.setattr(sh.LaneState, "_apply", real)
    mig.migrate_digest_backlog(sh, st, inventory=inv, prunes={"rows": []}, dry_run=False, now=NOW)
    lane = sh.LaneState(st.root).get(FP)
    assert lane["status"] == "fixed"
    assert lane["backlog_evidence"]["occurrence_count"] == 45
    assert not pending_rows(st)
    before = (st.root / "selfheal_lane.json").read_bytes()
    mig.migrate_digest_backlog(sh, st, inventory=inv, prunes={"rows": []}, dry_run=False, now=NOW)
    assert (st.root / "selfheal_lane.json").read_bytes() == before


def test_canonical_import_preserves_existing_key_and_original_backup(tmp_path, monkeypatch):
    st = store(tmp_path)
    canonical = "agent-canonical"
    monkeypatch.setattr(sh, "canonicalize", lambda d: {
        **d, "fingerprint": canonical, "canon_fingerprint": canonical,
        "raw_fingerprint": d["fingerprint"]})
    inv = inventory([comment(1), comment(2, detection(fingerprint="agent-alias", quote="other"))])
    mig.migrate_digest_backlog(sh, st, inventory=inv, prunes={"rows": []}, dry_run=False, now=NOW)
    assert list(pending_rows(st)) == [canonical]
    backup = (st.root / "selfheal_pending.sqlite.bak").read_bytes()
    inv["cards"][0]["comments"].append(comment(3, detection(fingerprint="agent-third", quote="third")))
    mig.migrate_digest_backlog(sh, st, inventory=inv, prunes={"rows": []}, dry_run=False, now=NOW)
    assert pending_rows(st)[canonical]["fingerprint"] == canonical
    assert (st.root / "selfheal_pending.sqlite.bak").read_bytes() == backup


def test_board_inventory_uses_ro_helper(tmp_path, monkeypatch):
    db = tmp_path / "kanban.db"
    con = sqlite3.connect(db)
    try:
        with con:
            con.execute("CREATE TABLE tasks (id TEXT, idempotency_key TEXT, status TEXT)")
            con.execute("CREATE TABLE task_comments (id INTEGER, task_id TEXT, body TEXT, created_at INTEGER)")
            con.execute("INSERT INTO tasks VALUES ('t_d', 'selfheal-digest:2026-11-18', 'done')")
            con.execute("INSERT INTO task_comments VALUES (5, 't_d', ?, ?)",
                        (comment_body(detection()), int(NOW)))
    finally:
        con.close()
    seen = []
    real = sh._ro_state

    def wrapped(path):
        seen.append(Path(path))
        return real(path)

    monkeypatch.setattr(sh, "_ro_state", wrapped)
    monkeypatch.setattr(sh, "board_db_path", lambda *a, **k: db)
    st = store(tmp_path)
    out = mig.migrate_digest_backlog(
        sh, st, inventory="board", prunes={"rows": []}, dry_run=True, now=NOW)
    assert seen and seen[0] == db
    assert out["queued"] == [FP]
