"""Daily cap covers legacy creation too; recovery never treats unknown counts as zero."""
import sqlite3
from pathlib import Path

import pytest
from test_self_heal_one_card import sh, env, defect, fill, file, events, NOW, MIDNIGHT, OPEN


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
    # Store the fake CLI's actual comments in a fixture-owned board, as the real CLI does.
    con = sqlite3.connect(env.board.db)
    try:
        con.execute("CREATE TABLE task_comments (task_id TEXT, body TEXT)")
        con.commit()
    finally:
        con.close()
    fill(env.board, 40)
    d = defect()
    out = file(env, d)
    con = sqlite3.connect(env.board.db)
    try:
        con.executemany("INSERT INTO task_comments VALUES (?, ?)", env.board.comments)
        con.commit()
    finally:
        con.close()
    (Path(env.store.root) / sh.DIGEST_STATE).unlink()
    sh._over_cap(d, d["fingerprint"], None, env.store, env.board, NOW+1, {})
    comments = [b for c,b in env.board.comments if c == out["digest"]]
    assert len(comments) == 1
