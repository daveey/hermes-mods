"""Session-owner adoption is re-judged, and a finished card never answers a later complaint (11-12 regression).

Supervisor chats 20261111_090650_1247e7 / 20261111_033213_db5894 link dozens of unrelated cards. On 11-11/11-12 the
lane adopted five of them as defect owners (fixture: tests/fixtures/selfheal_wrong_adoptions_1112.json, real
detections and card text from the hub). f83c4186 (11-12 11:50Z) tightened the rule for NEW adoptions only: the
lanes it had already approved stayed approved, so reconcile filed VERIFY cards against the wrong owners and each
verifier had to find the real fix by hand. And one case still passes today's rule: agent-6a8134f494 ("hub-only
placement rationale insufficiently audited", 18:49Z) shares "hub-only"/"audit" with a placement audit that had
already finished at 17:37Z.

Now: (1) a card finished before the complaint is never its owner; (2) reconcile re-judges every approved session
adoption made under an older rule before it waits on, or files a VERIFY for, that owner: a refused owner is
rejected (`selfheal_adoption_rejected`), the lane goes stale and the detection is queued for a fresh owner.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_self_heal_fix_placement import NOW, OPEN, core, sh, store  # noqa: F401 (fixture)

CASES = json.loads((Path(__file__).parent / "fixtures" / "selfheal_wrong_adoptions_1112.json").read_text())


def defect(case, fp):
    det = case["detect"]
    return {"fingerprint": fp, "component": det["component"], "symptom": det["symptom"], "source": det["source"],
            "session_id": det["session_id"], "evidence": det["evidence"], "quote": det["evidence"],
            "kind": "agent_fault", "at": det["ts"], "complaint_at": det["ts"]}


class Board:
    """Fake `hermes kanban`: `show --json` returns the given cards' full text; create returns sequential ids."""

    def __init__(self, cards):
        self.cards, self.calls, self.n = {c["id"]: c for c in cards}, [], 0

    def __call__(self, argv, capture_output=True, text=True, input=None, timeout=None):
        self.calls.append(list(argv))
        if "--board" in argv and ":" in argv[argv.index("--board") + 1]:  # the real CLI's slug check (exit 2)
            return SimpleNamespace(returncode=2, stdout="", stderr="kanban: invalid board slug")
        if "create" in argv:
            self.n += 1
            return SimpleNamespace(returncode=0, stdout=json.dumps({"id": f"t_new{self.n:05d}"}), stderr="")
        if "show" in argv:
            card = self.cards.get(argv[argv.index("show") + 1])
            if not card:
                return SimpleNamespace(returncode=1, stdout="", stderr="not found")
            return SimpleNamespace(returncode=0, stdout=json.dumps({"task": {"status": "done", **card}}), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def creates(self):
        return [a for a in self.calls if "create" in a]


def events(store, kind):
    p = Path(store.root) / "log.jsonl"
    rows = map(json.loads, p.read_text().splitlines()) if p.exists() else []
    return [r for r in rows if r.get("event") == kind]


def legacy_adoption(store, fp, case, board=None):
    """The lane as the hub held it on 11-12: approved on a session card, no adopt_* fields (adopted before the rule
    was recorded), plus its detect and adoption rows in the coach log."""
    det, owner = case["detect"], case["wrong_owner"]
    store.log({"event": "selfheal_detect", "mode": "live", "fingerprint": fp, **det})
    store.log({"event": "selfheal_adopted_owner", "fingerprint": fp, "card": owner["id"], "status": "ready",
               "session_id": det["session_id"], "candidates": [owner["id"]]})
    sh.LaneState(store.root).update(fp, status="approved", fix_card=owner["id"], approved_at=case["adopted_at"],
                                    component=det["component"], symptom=det["symptom"],
                                    session_id=det["session_id"], filed_day="2026-11-12",
                                    cards=[owner["id"]], complaint_at=det["ts"],
                                    **({"board": board} if board else {}))


def pending(store):
    db = sh._pending_db(store)
    try:
        return {fp: json.loads(p) for fp, p in db.execute("SELECT fp, payload FROM pending")}
    finally:
        db.close()


@pytest.mark.parametrize("fp", ["cron-c6a6e6bc09", "agent-6a8134f494", "agent-4f525369e7",
                                "kanban-625e9af794", "agent-1a84ca4de2"])
def test_wrong_session_adoption_is_rejected_at_reconcile_not_verified(store, monkeypatch, fp):
    monkeypatch.setattr(sh, "budget", OPEN)
    case = CASES[fp]
    legacy_adoption(store, fp, case)
    board = Board([case["wrong_owner"]])
    now = case["detect"]["ts"] + 86400  # the next day, while the detection is still inside the pending window
    sh.reconcile(store, runner=board, now=now)
    st = sh.LaneState(store.root).get(fp)
    assert not board.creates(), "no VERIFY card may be filed against an unrelated owner"
    assert st["status"] == "stale" and not st.get("fix_card") and not st.get("verify_card")
    assert case["wrong_owner"]["id"] in st["rejected_owners"]
    rej = events(store, "selfheal_adoption_rejected")
    assert [r["card"] for r in rej] == [case["wrong_owner"]["id"]] and rej[0]["requeued"]
    queued = pending(store)[fp]
    assert queued["symptom"] == case["detect"]["symptom"] and queued["session_id"] == case["detect"]["session_id"]


def test_rejected_owner_is_never_adopted_again(store):
    fp = "agent-6a8134f494"
    case = CASES[fp]
    d = defect(case, fp)
    owner = dict(case["wrong_owner"], status="done", board=None, fix_meta=False, completed_at=None)
    sh.LaneState(store.root).update(fp, status="stale", rejected_owners=[owner["id"]])
    assert sh._adopt_session_card(d, fp, store, case["adopted_at"],
                                  owner_fn=lambda sid, since: [dict(owner)]) is None
    assert events(store, "selfheal_adopt_skipped")[0]["rejected_owners"] == [owner["id"]]


def test_card_finished_before_the_complaint_is_not_its_owner(store):
    """agent-6a8134f494: the placement audit finished at 17:37Z, the complaint came at 18:49Z."""
    fp = "agent-6a8134f494"
    case = CASES[fp]
    d = defect(case, fp)
    owner = dict(case["wrong_owner"], status="done", board=None, fix_meta=False)
    assert owner["completed_at"] < d["complaint_at"]
    assert sh.session_owner_refusal(owner, d, fp) == sh.REFUSE_FINISHED_BEFORE
    assert sh._adopt_session_card(d, fp, store, case["adopted_at"],
                                  owner_fn=lambda sid, since: [dict(owner)]) is None
    assert events(store, "selfheal_adopt_skipped")[0]["finished_before"] == [owner["id"]]


@pytest.mark.parametrize("fp", sorted(CASES))
def test_none_of_the_1112_owners_is_adopted_fresh(store, fp):
    case = CASES[fp]
    d = defect(case, fp)
    owner = dict(case["wrong_owner"], status="ready", board=None, fix_meta=False)
    if owner["completed_at"] and owner["completed_at"] > case["adopted_at"]:
        owner["completed_at"] = None  # still open when the lane adopted it
    assert sh._adopt_session_card(d, fp, store, case["adopted_at"],
                                  owner_fn=lambda sid, since: [dict(owner)]) is None


def matching_case(store):
    fp = "kanban-0123abcdef"
    d = {"fingerprint": fp, "component": "kanban", "symptom": "ready cards not scheduled despite free worker slots",
         "source": "complaint", "session_id": "s-long", "evidence": "why are the ready cards not scheduled",
         "quote": "why are the ready cards not scheduled", "at": NOW - 7200, "complaint_at": NOW - 7200}
    owner = {"id": "t_match0001", "title": "Dispatcher: ready cards not scheduled while worker slots are free",
             "body": "The dispatcher leaves ready cards unscheduled although worker slots are free.",
             "idempotency_key": None, "created_at": NOW - 7000, "completed_at": NOW - 3600}
    store.log({"event": "selfheal_detect", "mode": "live", "fingerprint": fp, "ts": d["at"],
               **{k: d[k] for k in ("session_id", "source", "component", "symptom", "evidence")}})
    return fp, d, owner


def test_matching_owner_survives_revalidation_and_is_stamped(store, monkeypatch):
    monkeypatch.setattr(sh, "budget", OPEN)
    fp, d, owner = matching_case(store)
    assert sh.session_owner_refusal(owner, d, fp) is None
    store.log({"event": "selfheal_adopted_owner", "fingerprint": fp, "card": owner["id"], "session_id": "s-long"})
    sh.LaneState(store.root).update(fp, status="approved", fix_card=owner["id"], approved_at=NOW - 7100,
                                    component="kanban", symptom=d["symptom"], session_id="s-long",
                                    complaint_at=d["complaint_at"])
    sh.reconcile(store, runner=Board([owner]), now=NOW)
    st = sh.LaneState(store.root).get(fp)
    assert st["fix_card"] == owner["id"] and st["adopt_rule"] == sh.ADOPT_RULE and st["adopt_via"] == "session"
    assert not events(store, "selfheal_adoption_rejected")
    assert [r["ok"] for r in events(store, "selfheal_adoption_revalidated")] == [True]


def test_unreadable_owner_is_kept_and_rechecked_hourly(store, monkeypatch):
    monkeypatch.setattr(sh, "budget", OPEN)
    fp, d, owner = matching_case(store)
    store.log({"event": "selfheal_adopted_owner", "fingerprint": fp, "card": owner["id"], "session_id": "s-long"})
    sh.LaneState(store.root).update(fp, status="approved", fix_card=owner["id"], component="kanban",
                                    symptom=d["symptom"], session_id="s-long")
    board = Board([])  # show fails
    sh.reconcile(store, runner=board, now=NOW)
    sh.reconcile(store, runner=board, now=NOW + 60)
    st = sh.LaneState(store.root).get(fp)
    assert st["status"] == "approved" and st["fix_card"] == owner["id"]
    assert [r["ok"] for r in events(store, "selfheal_adoption_revalidated")] == [None]


def test_new_session_adoption_records_the_current_rule(store):
    fp, d, owner = matching_case(store)
    r = sh._adopt_session_card(d, fp, store, NOW, owner_fn=lambda sid, since: [dict(owner, status="ready",
                                                                                       board=None, fix_meta=False,
                                                                                       completed_at=None)])
    assert r and r["card"] == owner["id"]
    st = sh.LaneState(store.root).get(fp)
    assert (st["adopt_via"], st["adopt_card"], st["adopt_rule"]) == ("session", owner["id"], sh.ADOPT_RULE)


def test_filed_lane_is_not_revalidated(store, monkeypatch):
    """A lane whose owner the lane filed itself (no adoption row) is never re-judged as a session adoption."""
    monkeypatch.setattr(sh, "budget", OPEN)
    fp, d, owner = matching_case(store)
    sh.LaneState(store.root).update(fp, status="approved", fix_card="t_filed0001", component="kanban",
                                    symptom="something unrelated entirely", session_id="s-long")
    sh.reconcile(store, runner=Board([dict(owner, id="t_filed0001", title="x", body="y")]), now=NOW)
    st = sh.LaneState(store.root).get(fp)
    assert st["adopt_via"] == "unknown" and st["fix_card"] == "t_filed0001"
    assert not events(store, "selfheal_adoption_rejected")


@pytest.mark.parametrize("fp", ["cron-c6a6e6bc09", "agent-6a8134f494", "agent-4f525369e7"])
def test_digest_board_owner_is_read_and_rejected_not_kept_unreadable(store, monkeypatch, fp):
    """Live 11-13 04:09Z: 46 of 48 legacy session lanes carry the digest's '<gateway>:<board>' (hermes-hub:default).
    `hermes kanban --board hermes-hub:default` exits 2 (invalid slug), so every such owner read as unreadable and was
    kept; none was rejected. The owner text is read with the board part only."""
    monkeypatch.setattr(sh, "budget", OPEN)
    case = CASES[fp]
    legacy_adoption(store, fp, case, board="hermes-hub:default")
    board = Board([case["wrong_owner"]])
    sh.reconcile(store, runner=board, now=case["detect"]["ts"] + 86400)
    shows = [a for a in board.calls if "show" in a and case["wrong_owner"]["id"] in a]
    assert shows and all(":" not in a[a.index("--board") + 1] for a in shows if "--board" in a)
    assert [r["card"] for r in events(store, "selfheal_adoption_rejected")] == [case["wrong_owner"]["id"]]
    assert not [r for r in events(store, "selfheal_adoption_revalidated") if r.get("ok") is None]


def test_owner_card_text_board_slug():
    board = Board([{"id": "t_x", "title": "T", "body": "B"}])
    assert sh._owner_card_text("t_x", "hermes-hub:default", board)["title"] == "T"
    assert board.calls[-1][:4] == ["hermes", "kanban", "--board", "default"]
    assert sh._owner_card_text("t_x", "other-gw:", board)["title"] == "T"
    assert "--board" not in board.calls[-1]


def test_requeued_detection_names_the_session_its_quote_came_from(store, monkeypatch):
    """agent-e34c558227 (11-13): the lane held its FIRST session (10-28, "stuck?"); the newest detection came from
    another session (11-10 22:43Z, "resume"). The revalidation requeue took session_id from the lane but quote/time
    from the newest detection, so the refiled card linked a transcript that never contained its quote. Every
    provenance field (session, quote, time, host, profile) must come from one detection row, stamped `detect_ts`."""
    monkeypatch.setattr(sh, "budget", OPEN)
    fp = "agent-6a8134f494"
    case = CASES[fp]
    legacy_adoption(store, fp, case)  # lane + first detect row: session A
    det_a = case["detect"]
    det_b = {**det_a, "ts": det_a["ts"] + 3600, "session_id": "20261111_183249_2b0f34", "evidence": "resume",
             "host": "hub-b", "profile": "ops"}
    store.log({"event": "selfheal_detect", "mode": "live", "fingerprint": fp, **det_b})
    # a later filed row (no session/evidence) must not lend its own ts to the requeued detection
    store.log({"event": "selfheal_filed", "fingerprint": fp, "card": "t_filedlater", "component": det_a["component"],
               "source": det_a["source"], "ts": det_b["ts"] + 600})
    sh.reconcile(store, runner=Board([case["wrong_owner"]]), now=det_a["ts"] + 86400)
    queued = pending(store)[fp]
    assert queued["session_id"] == det_b["session_id"], "session must come from the record the quote came from"
    assert queued["quote"] == "resume" and queued["evidence"] == "resume"
    assert queued["at"] == det_b["ts"] and queued["detect_ts"] == det_b["ts"]
    assert queued["host"] == "hub-b" and queued["profile"] == "ops"
    brief = sh._investigation_brief(dict(queued))
    assert "session: @session:ops/20261111_183249_2b0f34" in brief and 'Quote: "resume"' in brief
    rej = events(store, "selfheal_adoption_rejected")[0]
    assert rej["session_id"] == det_b["session_id"]


def test_detect_record_takes_provenance_from_one_row(store):
    """The newest selfheal_detect row supplies session/quote/time whole: an empty field there is never back-filled
    from an older row, and a later selfheal_filed row never moves `ts`."""
    fp = "agent-0123456789"
    store.log({"event": "selfheal_detect", "fingerprint": fp, "source": "complaint", "session_id": "s-old",
               "evidence": "stuck?", "ts": 100.0, "host": "old-host"})
    store.log({"event": "selfheal_detect", "fingerprint": fp, "source": "backstop", "session_id": None,
               "evidence": "cron died", "ts": 200.0})
    store.log({"event": "selfheal_filed", "fingerprint": fp, "source": "backstop", "card": "t_c", "ts": 300.0})
    rec = sh._detect_record(store, fp)
    assert rec["evidence"] == "cron died" and rec["ts"] == 200.0 and rec["detect_ts"] == 200.0
    assert "session_id" not in rec and "host" not in rec and rec["card"] == "t_c"
    assert sh._adoption_log_records(store, {fp})[fp]["detect"] == rec
