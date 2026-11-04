"""One long quote fanned out over sibling sessions files one card (agent-ea7ff00170).

Each wake of a standing Kanban card is its own session, so the ask-gate gave every wake its own fingerprint and the
same injected card body (one complaint_quote_hash) became 39 Opus cards. A long quote shared across sessions is the
same evidence; a short one ("so, can you fix it please") may be two real complaints and still files twice
(test_self_heal_complaint_turn.test_same_quote_other_session_is_not_adopted).
"""
from test_self_heal_lane import HUB, NOW, OPEN, Runner, defect, ev, sh, store  # noqa: F401
import pytest

CARD_QUOTE = ("# Kanban task t_01a184cee94603eb: Standing: support Eve on askeve. dont ask me to approve unless she "
              "tries to do things that dont involve askeve repo / infra. Wake every 30 minutes and read her channel.")


def file(d, store, runner, now=NOW):
    return sh.file_defect(d, store=store, m="live", runner=runner, now=now,
                          cfg=HUB, budget_fn=OPEN, owner_fn=lambda *a: [], sig_owner_fn=lambda *a: [])


def wake(n, quote=CARD_QUOTE):
    # Same quote, different session and fingerprint per wake (the ask-gate's per-session fingerprint).
    d = defect("agent", "agent asked David to do it himself", source="ask-gate", quote=quote,
               session_id=f"local-wake-{n}", at=NOW + n * 1800)
    d["fingerprint"] = f"agent-wake{n:06d}"
    return d


def test_long_quote_in_sibling_sessions_files_one_card(store):
    runner = Runner()
    first = file(wake(0), store, runner)
    assert first["action"] == "filed"
    for n in (1, 2, 3):
        out = file(wake(n), store, runner, NOW + n * 1800)
        assert out["action"] == "skip" and out["card"] == first["card"], out
        assert out["why"].startswith("same quote as ")
    assert len(runner.creates()) == 1
    adopted = [e for e in ev(store, "selfheal_adopted_owner") if e.get("via") == "complaint_quote"]
    assert len(adopted) == 3


def test_long_quote_files_again_once_the_owner_is_closed(store):
    runner = Runner()
    first = file(wake(0), store, runner)
    assert first["action"] == "filed"
    sh.record_event("fixed", wake(0)["fingerprint"], "t_abcdef04", store=store, now=NOW + 10)
    assert file(wake(1), store, runner, NOW + 1800)["action"] == "filed"
    assert len(runner.creates()) == 2


@pytest.mark.parametrize("quote", ["so, can you fix it please", "why did you stop? " * 3])
def test_short_quote_in_other_sessions_still_files(store, quote):
    runner = Runner()
    file(wake(0, quote), store, runner)
    assert file(wake(1, quote), store, runner, NOW + 1800)["action"] == "filed"
    assert len(runner.creates()) == 2
