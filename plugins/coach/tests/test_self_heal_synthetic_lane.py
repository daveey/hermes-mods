"""A defect withdrawn as synthetic closes its lane, so an already-adopted owner never gets a VERIFY (kanban-0c3cff01cc).

Hub, 11-12: a complaint row deferred at the cap was refiled before the classifier knew its "⏳ [default]" tag; the
origin session's newest card (an unrelated review) was adopted as owner and the lane went `approved`. The next
retry_deferred pass withdrew the pending row as synthetic but left the lane approved, so reconcile filed a VERIFY
when that card finished. Withdrawal now closes the lane, and receipt recovery refuses a synthetic detection.
"""
import json
import sqlite3

from test_self_heal_fix_placement import Board, FP, NOW, OPEN, core, sh, store  # noqa: F401 (fixture)

SYNTH = ("\u23f3 [default] @default Kanban t_0b82dcda: Waiting 27276 min on parent t_5408b241 (example card), "
         "which has been ready 30 min with no worker (23 card(s) ahead of it, 6/7 worker slots busy).")
REAL = "the board says the card is ready but nothing ever picks it up, still broken"


def detect(store, quote):
    store.log({"event": "selfheal_detect", "mode": "live", "session_id": "s1", "source": "complaint",
               "fingerprint": FP, "component": "kanban", "symptom": "Ready card stuck with no worker",
               "evidence": quote})


def adopt(store):
    sh.LaneState(store.root).update(FP, status="approved", fix_card="t_abcdef01", approved_at=NOW - 3600,
                                    component="kanban", symptom="Ready card stuck with no worker", session_id="s1")


def pend(store, quote):
    d = {"fingerprint": FP, "component": "kanban", "symptom": "Ready card stuck with no worker",
         "source": "complaint", "session_id": "s1", "quote": quote, "at": NOW - 600, "p_defect": 0.8}
    db = sh._pending_db(store)
    with db:
        db.execute("INSERT INTO pending VALUES (?, ?, ?, ?)", (FP, json.dumps(d), NOW - 60, "v1"))
    db.close()
    return d


def events(store, kind):
    return [r for r in map(json.loads, (store.root / "log.jsonl").read_text().splitlines()) if r.get("event") == kind]


def never_file(d, **k):
    raise AssertionError(f"the filer must not run for {d['fingerprint']}")


def test_adopted_lane_withdrawn_by_retry_pass_gets_no_verify(store, monkeypatch):
    monkeypatch.setattr(sh, "budget", OPEN)
    detect(store, SYNTH)
    adopt(store)
    pend(store, SYNTH)
    out = sh.retry_deferred(store, now=NOW, filer=never_file)
    assert {"action": "skip", "why": "synthetic_tag"} in out
    st = sh.LaneState(store.root).get(FP)
    assert st["status"] == "not_defect" and st["closed_at"] == NOW
    assert events(store, "selfheal_withdrawn")[-1]["lane_closed"] == "approved"
    board = Board(status={"t_abcdef01": "done"})
    sh.reconcile(store, runner=board, now=NOW + 60)
    assert not board.creates()
    assert not events(store, "selfheal_verify_filed")


def test_receipt_recovery_refuses_a_synthetic_detection(store, monkeypatch):
    """A lane left approved by an older withdrawal (before this fix) is closed at reconcile, not verified."""
    monkeypatch.setattr(sh, "budget", OPEN)
    detect(store, SYNTH)
    adopt(store)
    board = Board(status={"t_abcdef01": "done"})
    done = sh.reconcile(store, runner=board, now=NOW)
    assert not board.creates()
    assert done == [(FP, "withdrawn (synthetic_tag): no receipt verification")]
    assert sh.LaneState(store.root).get(FP)["status"] == "not_defect"
    w = events(store, "selfheal_withdrawn")[-1]
    assert w["stage"] == "receipt" and w["fix_card"] == "t_abcdef01"


def test_file_defect_withdrawal_closes_an_adopted_lane(store):
    detect(store, SYNTH)
    adopt(store)
    d = {"fingerprint": FP, "component": "kanban", "symptom": "Ready card stuck with no worker",
         "source": "complaint", "session_id": "s1", "quote": SYNTH, "at": NOW, "p_defect": 0.8}
    assert sh.file_defect(d, store=store, m="live", cfg={}, now=NOW) == {"action": "skip", "why": "synthetic_tag"}
    assert sh.LaneState(store.root).get(FP)["status"] == "not_defect"


def test_real_complaint_lane_survives_a_synthetic_row_on_its_fingerprint(store, monkeypatch):
    """A fingerprint is component+symptom: a synthetic row sharing it must not close a real complaint's lane."""
    monkeypatch.setattr(sh, "budget", OPEN)
    detect(store, REAL)
    adopt(store)
    pend(store, SYNTH)
    sh.retry_deferred(store, now=NOW, filer=never_file)
    assert sh.LaneState(store.root).get(FP)["status"] == "approved"
    assert "lane_closed" not in events(store, "selfheal_withdrawn")[-1]
    board = Board(status={"t_abcdef01": "done"})
    sh.reconcile(store, runner=board, now=NOW + 60)
    assert len(board.creates()) == 1  # the real owner still gets its receipt verifier


def test_settled_lane_keeps_its_verdict(store):
    detect(store, SYNTH)
    sh.LaneState(store.root).update(FP, status="fixed", fixed_at=NOW - 10)
    pend(store, SYNTH)
    sh.retry_deferred(store, now=NOW, filer=never_file)
    assert sh.LaneState(store.root).get(FP)["status"] == "fixed"
