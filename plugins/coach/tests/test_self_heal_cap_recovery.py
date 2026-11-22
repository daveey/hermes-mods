"""Daily cap covers legacy creation too; recovery never treats unknown counts as zero."""
import sqlite3
from pathlib import Path

import pytest
from test_self_heal_one_card import sh, env, defect, fill, file, events, NOW, MIDNIGHT, OPEN, load, ROOT


def legacy(env):
    d = defect(component="kanban", symptom="legacy cap")
    fp = d["fingerprint"]
    sh.LaneState(env.store.root).update(fp, status="proposed", component="kanban", symptom=d["symptom"],
                                      card="t_old", gen=1, filed_at=NOW-3600, proposed_at=NOW-60)
    path = Path(env.store.root) / "selfheal" / fp
    path.mkdir(parents=True)
    (path / "proposal.md").write_text("Title\n\nPlan: repair the defect\n")
    return fp


@pytest.mark.parametrize("count", [39, 40])
def test_legacy_fix_verify_respects_cap_and_resumes_tomorrow(env, count):
    fp = legacy(env)
    fill(env.board, count)
    sh.file_fix(fp, store=env.store, runner=env.board, now=NOW, budget_fn=OPEN)
    assert sh.cards_created_today(None, NOW) == 40
    st = sh.LaneState(env.store.root).get(fp)
    assert not st.get("verify_card")
    assert bool(st.get("fix_card")) == (count == 39)
    assert events(env.store, "selfheal_over_cap")
    env.board.now = MIDNIGHT + 60
    sh.file_fix(fp, store=env.store, runner=env.board, now=MIDNIGHT+60, budget_fn=OPEN)
    st = sh.LaneState(env.store.root).get(fp)
    assert st["fix_card"] and st["verify_card"]


@pytest.mark.parametrize("column", ["created_by", "created_at"])
def test_existing_uncountable_schema_is_not_zero(env, column):
    con = sqlite3.connect(env.board.db)
    try:
        con.execute(f"ALTER TABLE tasks DROP COLUMN {column}")
        con.commit()
    finally:
        con.close()
    d = defect()
    out = sh._over_cap(d, d["fingerprint"], None, env.store, env.board, NOW, {})
    assert out and out["action"] == "error"
    assert not env.board.creates()


def test_digest_memo_recovers_persisted_fingerprints(env):
    fill(env.board, 40)
    d = defect()
    out = file(env, d)
    # A fresh process reads the canonical report, not a board comment or memo.
    restarted = load("cap_recovery_restart", ROOT / "self_heal.py")
    second = restarted._over_cap(d, d["fingerprint"], None, env.store, env.board, NOW+1, {})
    assert second["digest_report"]["count"] == 1
    assert second["digest_report"]["path"] == out["digest_report"]["path"]
    assert env.board.calls == []


def test_digest_unreadable_comments_after_memo_loss_fails_closed(env):
    """Unreadable canonical report is visible, never replaced with an empty report."""
    fill(env.board, 40)
    d = defect()
    first = file(env, d)
    path = Path(first["digest_report"]["path"])
    path.write_text("not JSON")
    out = sh._over_cap(d, d["fingerprint"], None, env.store, env.board, NOW+1, {})
    assert out["over_cap"] is True and out["digest"] is None and out["digest_report"] is None
    assert "digest report unavailable" in out["error"]
    assert "digest report unavailable" in events(env.store, "selfheal_over_cap")[-1]["error"]
    assert path.read_text() == "not JSON" and env.board.calls == []
    with sh.contextlib.closing(sqlite3.connect(Path(env.store.root) / "selfheal_pending.sqlite")) as db:
        assert db.execute("SELECT fp FROM pending").fetchall() == [(d["fingerprint"],)]


@pytest.mark.parametrize("kind", ["fix", "verify"])
def test_legacy_keyed_retry_at_cap_returns_existing_card(env, monkeypatch, kind):
    # Key lookup scans HERMES_HOME; cap counting uses HERMES_KANBAN_HOME.
    # Point both real readers at the same fixture-owned board.
    monkeypatch.setattr(sh, "_kanban_home", lambda: env.root)
    fp = legacy(env)
    lane = sh.LaneState(env.store.root)
    key = (sh.fix_key if kind == "fix" else sh.verify_key)(fp, 1)
    env.board.add("t_aabbccdd", key=key)
    fill(env.board, 39)
    assert sh.cards_created_today(None, NOW) == 40
    if kind == "fix":
        sh.file_fix(fp, store=env.store, runner=env.board, now=NOW, budget_fn=OPEN)
    else:
        assert sh._file_verify(fp, lane.get(fp), "t_fix", {"board": None},
                               env.store, env.board, NOW) == "t_aabbccdd"
    assert lane.get(fp)[f"{kind}_card"] == "t_aabbccdd"
    assert sh.cards_created_today(None, NOW) == 40
    assert len(env.board.rows("idempotency_key = ?", (key,))) == 1
    assert not [e for e in events(env.store, "selfheal_over_cap") if e["kind"] == kind]


@pytest.mark.parametrize("kind", ["fix", "verify"])
@pytest.mark.parametrize("location", [{"board": "default", "gateway": "remote"},
                                      {"board": "other"}])
def test_foreign_keyed_card_does_not_exempt_local_cap(env, monkeypatch, kind, location):
    fp = legacy(env)
    fill(env.board, 40)
    key = (sh.fix_key if kind == "fix" else sh.verify_key)(fp, 1)
    monkeypatch.setattr(sh, "keyed_cards", lambda key, runner: [{"id": "t_foreign", **location}])
    result = sh._legacy_creation_cap(fp, sh.LaneState(env.store.root).get(fp),
                                     {"board": None, "idempotency_key": key},
                                     env.store, env.board, NOW, kind)
    assert result["over_cap"] is True
    assert result["action"] == "skip"
    assert events(env.store, "selfheal_over_cap")[-1]["kind"] == kind
