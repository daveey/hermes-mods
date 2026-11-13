"""Adopting another lane's INVESTIGATION card waits for that lane's fix card.

2026-11-13: agent-0f69b9e1b0 adopted t_01a1d5a7cd1406c0 by error signature. That card was agent-6ba9ac9880's
investigation (its lane `card`; status proposed, fix deferred by concurrency), but the adopter stored it as its
`fix_card` with status approved. When the investigation completed with only a proposal, reconcile's receipt path
filed VERIFY t_01a1d5f199d422fe on it: a verifier with nothing to verify, which had to hand-file the real fix.
"""
import json

import pytest

from test_self_heal_fix_placement import Board, NOW, OPEN, sh, store  # noqa: F401

OWNER = "agent-6ba9ac9880"
ADOPTER = "agent-0f69b9e1b0"
INV = "t_01a1d5a7cd1406c0"
SYMPTOM = ("OperationalError in _execute_write (hermes_state.py): api_content backfill failed for "
           "session=20261028_221818_fdf44b")


def events(store, kind, fp=None):
    path = store.root / "log.jsonl"
    rows = [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []
    return [r for r in rows if r.get("event") == kind and (fp is None or r.get("fingerprint") == fp)]


def payload(fp):
    return {"component": "agent", "symptom": SYMPTOM, "source": "backstop", "kind": "hermes_bug", "p_defect": 0.8,
            "session_id": "20261113_170000_aaaaaa", "host": "hermes-hub", "fingerprint": fp}


def owner_proposed(store):
    sh.LaneState(store.root).update(OWNER, status="proposed", card=INV, fix_card=None, component="agent",
                                    symptom=SYMPTOM, filed_at=NOW - 3600, proposed_at=NOW - 60, gen=1)


def adopt_by_signature(store):
    card = {"id": INV, "title": "Self-heal: investigate", "status": "ready", "created_at": NOW - 3600, "board": None}
    out = sh._adopt_signature_card(payload(ADOPTER), ADOPTER, store, NOW, sig_owner_fn=lambda sig: [dict(card)])
    assert out and out["card"] == INV and out["via"] == "signature"


def legacy_adoption(store):
    # The lane exactly as the hub held it before this fix (selfheal_lane.json, 11-13 17:27Z, verify not yet filed).
    sh.LaneState(store.root).update(ADOPTER, status="approved", fix_card=INV, adopt_card=INV, adopt_via="signature",
                                    approved_at=NOW - 300, component="agent", symptom=SYMPTOM, cards=[INV])


@pytest.mark.parametrize("adopt", [adopt_by_signature, legacy_adoption], ids=["adoption", "legacy_lane"])
def test_investigation_owner_is_followed_until_its_fix_card_completes(store, monkeypatch, adopt):
    monkeypatch.setattr(sh, "budget", OPEN)
    owner_proposed(store)
    adopt(store)
    lane = sh.LaneState(store.root)
    board = Board(status={INV: "done"})
    # The investigation completes with only a proposal: nothing to verify yet.
    sh.reconcile(store, runner=board, now=NOW + 60)
    sh.reconcile(store, runner=board, now=NOW + 120)
    assert not events(store, "selfheal_verify_filed", ADOPTER)
    st = lane.get(ADOPTER)
    assert st["status"] == "approved" and not st.get("fix_card") and not st.get("verify_card")
    assert st["follow_owner"] == OWNER
    # The owner's fix card is filed; while it runs the adopter still waits.
    r = sh.file_fix(OWNER, store=store, runner=board, now=NOW + 180, budget_fn=OPEN)
    assert r["action"] == "filed"
    fix = r["card"]
    sh.reconcile(store, runner=board, now=NOW + 240)
    assert not events(store, "selfheal_verify_filed", ADOPTER)
    assert lane.get(ADOPTER)["fix_card"] == fix
    # The fix card completes: exactly one VERIFY for the adopter, naming that fix card.
    board.status[fix] = "done"
    sh.reconcile(store, runner=board, now=NOW + 300)
    sh.reconcile(store, runner=board, now=NOW + 360)
    verifies = events(store, "selfheal_verify_filed", ADOPTER)
    assert len(verifies) == 1, verifies
    assert verifies[0]["fix_card"] == fix
    assert lane.get(ADOPTER)["verify_card"] == verifies[0]["card"]


def test_owner_ending_without_a_fix_card_makes_the_follower_stale(store, monkeypatch):
    monkeypatch.setattr(sh, "budget", OPEN)
    owner_proposed(store)
    adopt_by_signature(store)
    sh.LaneState(store.root).update(OWNER, status="not_defect", closed_at=NOW + 30)
    sh.reconcile(store, runner=Board(status={INV: "done"}), now=NOW + 60)
    st = sh.LaneState(store.root).get(ADOPTER)
    assert st["status"] == "stale" and not st.get("follow_owner")
    assert not events(store, "selfheal_verify_filed", ADOPTER)


def test_existing_verify_on_an_adopted_investigation_is_left_alone(store, monkeypatch):
    """agent-0f69b9e1b0 already has its verify (parented by hand on the real fix): reconcile must not rewrite it."""
    monkeypatch.setattr(sh, "budget", OPEN)
    owner_proposed(store)
    lane = sh.LaneState(store.root)
    lane.update(ADOPTER, status="approved", fix_card=INV, adopt_card=INV, adopt_via="signature",
                verify_card="t_01a1d5f199d422fe", approved_at=NOW - 300, component="agent", cards=[INV])
    before = lane.get(ADOPTER)
    board = Board(status={INV: "done", "t_01a1d5f199d422fe": "running"})
    sh.reconcile(store, runner=board, now=NOW + 60)
    assert lane.get(ADOPTER) == before
    assert not board.creates()


def test_adopting_a_fix_card_is_unchanged(store, monkeypatch):
    """The owner's FIX card (not its investigation) is adopted as fix_card, as before."""
    monkeypatch.setattr(sh, "budget", OPEN)
    sh.LaneState(store.root).update(OWNER, status="approved", card=INV, fix_card="t_0000f1c5", component="agent")
    card = {"id": "t_0000f1c5", "title": "Self-heal: fix", "status": "running", "created_at": NOW, "board": None}
    sh._adopt_signature_card(payload(ADOPTER), ADOPTER, store, NOW, sig_owner_fn=lambda sig: [dict(card)])
    st = sh.LaneState(store.root).get(ADOPTER)
    assert st["fix_card"] == "t_0000f1c5" and not st.get("follow_owner")
