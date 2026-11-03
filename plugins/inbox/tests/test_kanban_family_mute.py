"""The `kanban` family mute (inbox-muted:kanban): what got it muted is refused today, and an accepted owner ask
resets the streak so a real decision from one worker is not muted by unrelated old noise.

The two cards David archived untouched were posted before the ask contract (2026-10-28): a self-heal result
notice and a status-plus-ask "results" card. Both shapes are refused now; after them David accepted
human_request-derived asks, which must count as decisions.

Run like the other inbox tests (see test_api.py docstring).
"""
import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
HR = {"interaction": "approval", "why_human": "Only David approves stopping these instances",
      "action": "Approve stopping the seven retired VMs. Unblock = stop them; archive = keep them."}
SELF_ASK = {"interaction": "approval", "why_human": "spend", "action": "OK the $200 cap"}


def _load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / "store.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A real store and the shipped poster registry (no test extras: `kanban` keeps owner kanban)."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from hermes_state import SessionDB
    st = _load("_kanban_family_mute")
    db = SessionDB(db_path=tmp_path / "state.db")
    db._inbox_keep_open = True
    clock = {"t": 1_000_000.0}
    cards = []
    inbox = st.Inbox(home=tmp_path, db_factory=lambda: db, clock=lambda: clock["t"],
                     task_creator=lambda **kw: cards.append(kw) or "t_owner")
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_worker")
    hr = {"value": HR}
    monkeypatch.setattr(st, "card_human_request", lambda task_id: hr["value"])
    yield st, inbox, db, clock, cards, hr
    db.close()


def _post(inbox, clock, i, poster="kanban · some card"):
    clock["t"] += 10
    return inbox.post(f"Approve stopping batch {i}", "evidence", "Stop the instances", key=f"stop:{i}",
                      poster=poster)["session_id"]


def test_archived_selfheal_result_notice_is_refused_now(env):
    st, inbox, _, _, _, hr = env
    hr["value"] = None
    with pytest.raises(st.AgentOwnedPost):
        inbox.post("Fixed: Desktop backend no longer logs a dropped connection as a crash (ws receive failed)",
                   "result", "nothing", key="selfheal:desktop-2d2f8e30ac", type="selfheal-result",
                   poster="kanban · self-heal fix: RuntimeError in receive_text (websockets.py): ws receive")


def test_archived_status_with_self_written_ask_is_refused_now(env):
    st, inbox, _, _, _, hr = env
    hr["value"] = None  # the card had no human_request: the ask was the worker's own
    with pytest.raises(st.AgentOwnedPost, match="ask_self_attested"):
        inbox.post("Self-heal is live (proposal-only): results, cost, off switch. $200 cap needs your OK",
                   "48 h shadow results", "Approve the cap", key="selfheal-golive-result",
                   poster="kanban · self-heal 5/5: review 48 h shadow, go live #8", ask=SELF_ASK)


def test_accepted_owner_ask_resets_kanban_streak(env):
    """archived, accepted (Do it on a human_request ask), archived: the accept was David's decision."""
    st, inbox, _, clock, cards, _ = env
    inbox.archive(_post(inbox, clock, 0))
    inbox.act(_post(inbox, clock, 1), "do_it")
    inbox.archive(_post(inbox, clock, 2))
    assert _post(inbox, clock, 3)
    assert not [c for c in cards if c["idempotency_key"].startswith("inbox-muted:")]


def test_two_archived_owner_asks_still_mute_kanban(env):
    """The breaker still works for the family: two untouched archives in a row mute it and file the owner card."""
    st, inbox, _, clock, cards, _ = env
    inbox.archive(_post(inbox, clock, 0))
    inbox.archive(_post(inbox, clock, 1, poster="kanban · another card"))
    with pytest.raises(st.PosterMuted):
        _post(inbox, clock, 2, poster="kanban · third card")
    assert [c["idempotency_key"] for c in cards] == ["inbox-muted:kanban"]


def test_accept_without_ask_does_not_reset_kanban_streak(env):
    """A legacy (pre-contract) accepted card has no ask_json: still a generic Do it, as before."""
    st, inbox, _, clock, cards, _ = env
    inbox.archive(_post(inbox, clock, 0))
    sid = _post(inbox, clock, 1)
    inbox.act(sid, "do_it")
    with inbox._side() as side:
        side.execute("UPDATE items SET ask_json=NULL WHERE session_id=?", (sid,))
        side.commit()
    inbox.archive(_post(inbox, clock, 2))
    with pytest.raises(st.PosterMuted):
        _post(inbox, clock, 3)
