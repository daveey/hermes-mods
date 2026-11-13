"""A refused owner stays refused on the complaint-turn path too (11-13, live after 10739ac9/fc9e62c0).

Hub replay, session 20261111_213424_68cac3: agent-07ffde73f6 ("Claimed worker capacity without checking actual worker
load") was adopted onto t_01a1cca14f4dd08a (home-gateway upkeep scripts). kanban-c91f12bc2f, detected 78 s later in the
same turn, became its complaint-turn alias with a COPY of that card. At 04:09Z revalidation refused the card for
agent-07ffde73f6 (stale, rejected_owners=[card], detection requeued). At 04:22Z the retry ran `_adopt_complaint_lane`,
which skipped neither the lane's own alias nor its rejected owners: it re-adopted t_01a1cca14f4dd08a from the alias and
wrote complaint_owner=agent-07ffde73f6 onto agent-07ffde73f6 itself, a lane waiting on itself that is never refiled.
kanban-f213468fc5 likewise kept a copy of t_01a1cc1e1ffbe650 after kanban-d5a4be31a2 refused it.
"""
import json
from pathlib import Path

from test_self_heal_adopt_revalidate import Board, events, pending
from test_self_heal_fix_placement import OPEN, core, sh, store  # noqa: F401 (fixture)

SID = "20261111_213424_68cac3"
CARD = "t_01a1cca14f4dd08a"
AT = 1794436411.4206338
QHASH = "b0cac4a0737617da2c04"
REJECTED_AT = 1794542969.0
RETRY_AT = 1794543725.4


def detect_row(fp, symptom, component, ts):
    return {"event": "selfheal_detect", "mode": "live", "fingerprint": fp, "session_id": SID, "source": "admission",
            "p": 0.99, "component": component, "symptom": symptom, "ts": ts,
            "evidence": "claimed worker capacity; worker-1 idle, none checked"}


def hub_lanes_after_rejection(store):
    """selfheal_lane.json rows as the hub held them between the 04:09Z rejection and the 04:22Z retry."""
    lane = sh.LaneState(store.root)
    lane.update("agent-07ffde73f6", status="stale", fix_card=None, card=None, cards=[CARD],
                rejected_owners=[CARD], session_id=SID, component="agent",
                symptom="Claimed worker capacity without checking actual worker load",
                complaint_at=AT, complaint_quote_hash=QHASH, closed_at=REJECTED_AT, adopt_rule=1)
    lane.update("kanban-c91f12bc2f", status="adopted", fix_card=CARD, card=None, cards=[CARD],
                complaint_owner="agent-07ffde73f6", session_id=SID, component="kanban",
                symptom="Dependent cards remain ready without workers",
                complaint_at=AT, complaint_quote_hash=QHASH, filed_at=AT + 78)


def retry_defect():
    return {"fingerprint": "agent-07ffde73f6", "component": "agent", "source": "admission",
            "symptom": "Claimed worker capacity without checking actual worker load", "session_id": SID,
            "kind": "agent_fault", "quote": "claimed worker capacity; worker-1 idle, none checked",
            "at": AT, "complaint_at": AT}


def test_requeued_lane_does_not_readopt_its_refused_card_from_its_own_alias(store):
    hub_lanes_after_rejection(store)
    got = sh._adopt_complaint_lane(retry_defect(), "agent-07ffde73f6", store, RETRY_AT)
    assert got is None, f"re-adopted the refused owner via {got and got.get('via')}: {got}"
    st = sh.LaneState(store.root).get("agent-07ffde73f6")
    assert st["status"] == "stale" and st.get("complaint_owner") != "agent-07ffde73f6"
    assert not events(store, "selfheal_adopted_owner")


def test_third_detection_in_the_turn_does_not_inherit_a_card_the_canonical_lane_refused(store):
    hub_lanes_after_rejection(store)
    d = dict(retry_defect(), fingerprint="agent-feedface00", symptom="said workers were free without looking")
    assert sh._adopt_complaint_lane(d, "agent-feedface00", store, RETRY_AT) is None


def test_alias_of_another_lane_still_follows_a_live_owner(store):
    """Unchanged behaviour: a second detector in the same turn shares the canonical lane's (unrefused) card."""
    hub_lanes_after_rejection(store)
    lane = sh.LaneState(store.root)
    lane.update("agent-07ffde73f6", status="approved", fix_card="t_01a1ccd2c8e99719", rejected_owners=[CARD])
    lane.update("kanban-c91f12bc2f", fix_card="t_01a1ccd2c8e99719")
    d = dict(retry_defect(), fingerprint="agent-feedface00", symptom="said workers were free without looking")
    got = sh._adopt_complaint_lane(d, "agent-feedface00", store, RETRY_AT)
    assert got and got["card"] == "t_01a1ccd2c8e99719" and got["via"] == "complaint_turn"


def test_reconcile_releases_the_self_alias_and_the_alias_on_a_refused_card(store, monkeypatch):
    """Repair of the state the 04:22Z retry left on the hub: both lanes go stale and their detections requeue."""
    monkeypatch.setattr(sh, "budget", OPEN)
    hub_lanes_after_rejection(store)
    lane = sh.LaneState(store.root)
    lane.update("agent-07ffde73f6", status="adopted", fix_card=CARD, complaint_owner="agent-07ffde73f6")
    store.log(detect_row("agent-07ffde73f6", "Claimed worker capacity without checking actual worker load",
                         "agent", AT))
    store.log(detect_row("kanban-c91f12bc2f", "Dependent cards remain ready without workers", "kanban", AT + 78))
    board = Board([{"id": CARD, "title": "Routine home-gateway upkeep runs as reviewed no-LLM scripts",
                    "body": "", "status": "scheduled"}])
    sh.reconcile(store, runner=board, now=RETRY_AT + 3600)
    for fp in ("agent-07ffde73f6", "kanban-c91f12bc2f"):
        st = lane.get(fp)
        assert st["status"] == "stale" and not st.get("fix_card") and not st.get("complaint_owner"), (fp, st)
        assert CARD in st["rejected_owners"]
    rej = {r["fingerprint"]: r for r in events(store, "selfheal_adoption_rejected")}
    assert rej["agent-07ffde73f6"]["by"] == "alias_release" and rej["agent-07ffde73f6"]["requeued"]
    assert rej["agent-07ffde73f6"]["why"] == "card refused for agent-07ffde73f6"
    assert rej["kanban-c91f12bc2f"]["why"] == "card refused for agent-07ffde73f6"
    assert set(pending(store)) >= {"agent-07ffde73f6", "kanban-c91f12bc2f"}
    assert not board.creates()
    # And the requeued retry now files a fresh owner instead of looping back onto the refused card.
    assert sh._adopt_complaint_lane(retry_defect(), "agent-07ffde73f6", store, RETRY_AT + 3700) is None


def test_reconcile_leaves_a_healthy_alias_alone(store, monkeypatch):
    monkeypatch.setattr(sh, "budget", OPEN)
    hub_lanes_after_rejection(store)
    lane = sh.LaneState(store.root)
    lane.update("agent-07ffde73f6", status="approved", fix_card="t_01a1ccd2c8e99719", rejected_owners=[])
    sh.reconcile(store, runner=Board([]), now=RETRY_AT + 3600)
    assert lane.get("kanban-c91f12bc2f")["status"] == "adopted"
    assert not [r for r in events(store, "selfheal_adoption_rejected") if r.get("by") == "alias_release"]


def test_reconcile_leaves_a_self_alias_on_an_unrefused_card_alone(store, monkeypatch):
    """Hub has six such lanes (bot-04a2cdffb8, kanban-9a4da756ba, ...): a same-turn re-detection of an owner that
    already had a card. Nothing refused that card, so refiling would reopen a resolved complaint."""
    monkeypatch.setattr(sh, "budget", OPEN)
    lane = sh.LaneState(store.root)
    lane.update("bot-04a2cdffb8", status="adopted", fix_card="t_01a1c6796afe27b7", complaint_owner="bot-04a2cdffb8",
                session_id=SID, symptom="x", component="bot", complaint_at=AT)
    sh.reconcile(store, runner=Board([]), now=RETRY_AT + 3600)
    assert lane.get("bot-04a2cdffb8")["status"] == "adopted"
    assert not events(store, "selfheal_adoption_rejected")
