"""Incident regression: six actual traceback tails, stripped of source identity."""
import json
from pathlib import Path

from test_self_heal_lane import sh, Runner, OPEN, HUB, NOW, defect, core
import pytest


@pytest.fixture
def store(tmp_path):
    return core.Store(tmp_path / "coach")


def burst():
    rows = json.loads(Path(__file__).with_name("sqlite_corruption_burst.json").read_text())
    assert len(rows) == 6
    return [defect(**r, source="signal", session_id="", host="example-host") for r in rows]


def file(d, store, runner, **kw):
    return sh.file_defect(d, store=store, runner=runner, m="live", cfg=HUB, budget_fn=OPEN, **kw)


def test_six_real_tracebacks_create_one_card_and_five_comments(store):
    r = Runner()
    for d in burst():
        file(d, store, r, now=d["at"] + 600)
    assert len(r.creates()) == 1
    comments = [a for a, _ in r.calls if "comment" in a]
    assert len(comments) == 5
    assert all("t_00000001" in a for a in comments)


@pytest.mark.parametrize("message", ["database disk image is malformed", "KanbanDbCorruptError: broken", "file is not a database"])
def test_corruption_identity_ignores_callsite_but_not_path_hour_host(message):
    d = burst()[0]
    a = sh.corruption_incident({**d, "details": message}, d["at"])
    assert a
    assert a == sh.corruption_incident({**d, "symptom": "different callsite", "details": message}, d["at"])
    for changes in ({"db_path": "/other/kanban.db"}, {"at": d["at"] + 3600}, {"host": "other-host"}):
        assert a != sh.corruption_incident({**d, "details": message, **changes}, d["at"])
    assert sh.corruption_incident({**d, "details": "database is locked"}, d["at"]) is None


def test_running_corrupt_db_title_is_adopted_and_commented(store, monkeypatch):
    d = burst()[0]
    monkeypatch.setattr(sh, "signature_owned_cards", lambda sig: [
        {"id": "t_abcdef01", "board": None, "status": "running"}])
    monkeypatch.delenv("COACH_SELFHEAL_NO_EXEC")
    monkeypatch.setattr(sh, "session_owned_cards", lambda *a: [])
    r = Runner()
    out = file(d, store, r, now=d["at"])
    assert out["adopted"] and out["card"] == "t_abcdef01"
    assert not r.creates()
    assert len([a for a, _ in r.calls if "comment" in a]) == 1


def test_corruption_owner_match_requires_running_same_db():
    sig = {"corruption_db": "~/.hermes/kanban.db"}
    assert sh.signature_matches(sig, "Hub kanban.db tree corruption")
    assert not sh.signature_matches(sig, "state.db corruption")
    assert not sh.signature_matches(sig, "runners/kanban.db corruption")
    assert not sh.signature_matches({"corruption_db": "/other/kanban.db"}, "kanban.db corruption")


def test_duplicate_event_adopts_owner_and_cli(store, monkeypatch, capsys):
    monkeypatch.setattr(sh, "_store", lambda s=None: store)
    with pytest.raises(ValueError, match="owner"):
        sh.record_event("duplicate", "kanban-test", store=store)
    assert sh.main(["event", "duplicate", "kanban-test", "--card", "t_12345678", "--owner", "t_abcdef01"]) == 0
    st = sh.LaneState(store.root).get("kanban-test")
    assert st["status"] == "approved" and st["fix_card"] == "t_abcdef01"
    assert not st.get("closed_at")
    assert "t_12345678" in st["cards"]
    assert "duplicate" in sh.investigation_brief(defect())


def test_actual_owner_query_filters_status_path_and_host(tmp_path):
    import sqlite3
    db = sqlite3.connect(tmp_path / "kanban.db")
    db.execute("CREATE TABLE tasks (id, title, body, status, created_at)")
    db.executemany("INSERT INTO tasks VALUES (?,?,?,?,?)", [
        ("t_11111111", "Hub kanban.db corruption", "", "running", 1),
        ("t_22222222", "runners/kanban.db corruption", "", "running", 2),
        ("t_33333333", "kanban.db corruption", "", "done", 3),
        ("t_44444444", "kanban.db corruption", "", "ready", 4),
        ("t_66666666", "Collapse kanban.db corruption duplicates", "", "running", 6),
        ("t_55555555", "unrelated", "kanban.db corruption", "running", 5)])
    db.commit()
    db.close()
    sig = {"corruption_db": "~/.hermes/kanban.db", "host": sh.host_name()}
    assert [c["id"] for c in sh.signature_owned_cards(sig, home=tmp_path)] == ["t_11111111", "t_66666666"]
    assert not sh.signature_owned_cards({**sig, "host": "remote-host"}, home=tmp_path)


def test_hour_and_db_boundaries_reach_filing(store):
    r = Runner()
    d = burst()[0]
    for changes in ({}, {"at": d["at"] + 3600}, {"db_path": "/other/kanban.db"}):
        file({**d, **changes}, store, r, now=d["at"])
    assert len(r.creates()) == 3
    assert all(sh.FP_RE.fullmatch(fp) for fp in sh.LaneState(store.root).read())


def test_parallel_burst_is_serialized(store):
    from concurrent.futures import ThreadPoolExecutor
    r = Runner()
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda d: file(d, store, r, now=d["at"]), burst()))
    assert len(r.creates()) == 1
    assert len([a for a, _ in r.calls if "comment" in a]) == 5


def test_comment_failure_does_not_spawn_card(store):
    r = Runner()
    d = burst()[0]
    file(d, store, r, now=d["at"])
    r.fail = True
    assert file(burst()[1], store, r, now=d["at"])["action"] == "error"
    assert len(r.creates()) == 1


def test_shadow_repeat_never_comments_or_creates(store):
    r = Runner()
    for d in burst():
        assert sh.file_defect(d, store=store, runner=r, m="shadow", cfg=HUB,
                              budget_fn=OPEN, now=d["at"])["action"] == "would_file"
    assert not r.calls


def test_path_normalization_and_non_kanban_missing_identity():
    d = burst()[0]
    for path in ("/home/example/.hermes/kanban.db", "/Users/example/.hermes/kanban.db", "~/.hermes/kanban.db"):
        assert sh.corruption_incident({**d, "db_path": path}, NOW)["db"] == "~/.hermes/kanban.db"
    assert sh.corruption_incident({**d, "component": "other"}, NOW) is None
    assert sh.corruption_incident({**d, "details": "file is not a database: /other/state.db"}, NOW)["db"] == "/other/state.db"


def _owner_board(tmp_path, rows):
    import sqlite3
    db = sqlite3.connect(tmp_path / "kanban.db")
    db.execute("CREATE TABLE IF NOT EXISTS tasks (id, title, body, status, created_at)")
    db.executemany("INSERT INTO tasks VALUES (?,?,?,?,?)", rows)
    db.commit()
    db.close()


def test_recovery_lock_holder_card_owns_the_incident_first(tmp_path):
    """`hermes kanban recover` records its card in <db>.recover.pid while it holds the lock: new malformed
    reports for that DB attach to the card doing the recovery, ahead of older title matches."""
    import os
    _owner_board(tmp_path, [("t_11111111", "Hub kanban.db corruption", "", "running", 1),
                            ("t_77777777", "Some unrelated worker card", "", "running", 7)])
    (tmp_path / "kanban.db.recover.pid").write_text(json.dumps(
        {"pid": os.getpid(), "task_id": "t_77777777", "session_id": "s1"}))
    sig = {"corruption_db": "~/.hermes/kanban.db", "host": sh.host_name()}
    assert [c["id"] for c in sh.signature_owned_cards(sig, home=tmp_path)] == ["t_77777777", "t_11111111"]


def test_recovery_holder_on_the_damaged_board_itself_still_owns_it(tmp_path):
    """The holder card often lives on the board being rebuilt, which may not be readable: it still owns it."""
    import os
    (tmp_path / "kanban" / "boards" / "ops").mkdir(parents=True)
    (tmp_path / "kanban" / "boards" / "ops" / "kanban.db").write_bytes(b"not a database" * 100)
    (tmp_path / "kanban" / "boards" / "ops" / "kanban.db.recover.pid").write_text(json.dumps(
        {"pid": os.getpid(), "task_id": "t_88888888"}))
    sig = {"corruption_db": "~/.hermes/kanban/boards/ops/kanban.db", "host": sh.host_name()}
    cards = sh.signature_owned_cards(sig, home=tmp_path)
    assert [c["id"] for c in cards] == ["t_88888888"] and cards[0]["board"] == "ops"


def test_dead_or_cardless_or_remote_recovery_holder_is_ignored(tmp_path):
    import os
    _owner_board(tmp_path, [("t_11111111", "Hub kanban.db corruption", "", "running", 1),
                            ("t_77777777", "Some unrelated worker card", "", "running", 7),
                            ("t_99999999", "Finished recovery", "", "done", 9)])
    sig = {"corruption_db": "~/.hermes/kanban.db", "host": sh.host_name()}
    pid_file = tmp_path / "kanban.db.recover.pid"
    for record in ({"pid": 2 ** 22 + 999, "task_id": "t_77777777"},  # holder process gone
                   {"pid": os.getpid()},                              # no card recorded
                   {"pid": os.getpid(), "task_id": "t_99999999"}):    # card already done
        pid_file.write_text(json.dumps(record))
        assert [c["id"] for c in sh.signature_owned_cards(sig, home=tmp_path)] == ["t_11111111"], record
    pid_file.write_text(json.dumps({"pid": os.getpid(), "task_id": "t_77777777"}))
    assert not sh.signature_owned_cards({**sig, "host": "remote-host"}, home=tmp_path)


def test_live_recovery_holder_takes_over_an_existing_incident_lane(store, monkeypatch):
    """End to end: the incident's lane already owns card t_00000001; once `hermes kanban recover` holds the
    lock for card t_77777777, the next malformed report comments there and the lane follows the holder."""
    d1, d2 = burst()[:2]
    r = Runner()
    file(d1, store, r, now=d1["at"] + 600)
    assert len(r.creates()) == 1
    monkeypatch.setattr(sh, "incident_holder_card", lambda d: {"id": "t_77777777", "board": None,
                                                                 "status": "running", "via": "recovery_lock"})
    out = file(d2, store, r, now=d2["at"] + 600)
    assert out["card"] == "t_77777777" and out.get("via") == "recovery_lock"
    comments = [a for a, _ in r.calls if "comment" in a]
    assert comments and "t_77777777" in comments[-1]
    assert len(r.creates()) == 1, "no new card"
    fp = sh.corruption_incident(d2, d2["at"])
    st = sh.LaneState(store.root).get("sqlitecorrupt-" + __import__("hashlib").sha1(
        json.dumps(fp, sort_keys=True).encode()).hexdigest()[:16])
    assert st["fix_card"] == "t_77777777" and "t_77777777" in st["cards"]


def test_live_recovery_holder_wins_over_a_family_lane_for_a_new_incident(store, monkeypatch):
    d = burst()[0]
    monkeypatch.setattr(sh, "_adopt_family_lane", lambda *a, **k: {"action": "skip", "card": "t_fa000001",
                                                                   "adopted": True, "via": "family"})
    monkeypatch.setattr(sh, "incident_holder_card", lambda d: {"id": "t_77777777", "board": None,
                                                                 "status": "running", "via": "recovery_lock"})
    r = Runner()
    out = file(d, store, r, now=d["at"])
    assert out["card"] == "t_77777777" and not r.creates()


def test_incident_holder_card_reads_the_local_home_only(tmp_path, monkeypatch):
    import os
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("COACH_SELFHEAL_NO_EXEC")
    _owner_board(tmp_path, [("t_77777777", "worker", "", "running", 7)])
    (tmp_path / "kanban.db.recover.pid").write_text(json.dumps({"pid": os.getpid(), "task_id": "t_77777777"}))
    d = {"incident": {"db": "~/.hermes/kanban.db", "host": sh.host_name(), "hour": 1}}
    assert sh.incident_holder_card(d)["id"] == "t_77777777"
    assert sh.incident_holder_card({"incident": {**d["incident"], "host": "remote-host"}}) is None
    assert sh.incident_holder_card({}) is None
