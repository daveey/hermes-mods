"""Logged rejected excerpts: session 20261026_154948_ea6cfd.
Full rejected drafts were not persisted. These are the exact gate-log spans,
not reconstructed full replies; the descriptive reconciliation is from the report.
"""
import pytest
from test_superseding_work import _board, _home, FILLER, hook

REPORTS = [
    "Four review rounds found nothing it allows that main refuses.",
    "- **Why ship now:** four review rounds found nothing the new code allows that main refuses.",
]
TITLE = "stash-worktree-guard: preserve exported repository variables through branch reset"
CARD = "t_0123456789abcdef"
ASK = "Should I ship the stash guard handling of exported variables after branching?"

@pytest.fixture
def related(hook, gate, monkeypatch):
    _board(_home(gate) / "kanban.db", FILLER + [dict(id=CARD, title=TITLE,
        body="stash-worktree-guard exported repository variables branch reset", status="running")])
    import sqlite3
    with sqlite3.connect(_home(gate) / "kanban.db") as c:
        c.execute("update tasks set result=? where id=?", ("Landed commit 55f526f5aa1234567890", CARD))
    monkeypatch.setattr(gate, "_user_msgs", lambda s: [TITLE])
    return hook

@pytest.mark.parametrize("report", REPORTS)
def test_logged_decision_report_not_an_ask(related, gate, report):
    assert gate._superseding_reply_hold("s1", report, 0, 2) is None


def test_neutral_wording_does_not_invent_a_denial(related, gate):
    held = gate._superseding_reply_hold("s1", ASK, 0, 2)
    assert held and held["hold"]
    assert "Your reply says there is no sign" not in held["nudge"]

@pytest.mark.parametrize("mention", [CARD, "55f526f5", "exported repository variables"])
def test_card_mentions_by_id_landed_sha_or_distinctive_title(related, gate, mention):
    assert not gate.superseding.matches(gate.core, ASK, "Already addressed by " + mention)


def test_rewrite_released_once_per_turn_not_per_text(related, gate):
    assert gate._superseding_reply_hold("s1", REPORTS[0] + " " + ASK, 0, 2)
    assert gate._superseding_reply_hold("s1", REPORTS[1] + " " + ASK, 1, 2) is None
    # A different session has its own budget.
    assert gate._superseding_reply_hold("s2", ASK, 0, 2)
    # Real turn-start hook resets the budget, even for identical user text.
    related["pre_llm_call"](session_id="s1", platform="cli", user_message="Review the guard")
    assert gate._superseding_reply_hold("s1", ASK, 0, 2)
    assert gate._superseding_reply_hold("s1", ASK + " Please approve.", 0, 2, surface="clarify") is None


def test_unrelated_sha_and_generic_title_words_do_not_credit(related, gate):
    for mention in ("deadbeef", "repository", "branch reset", "55f526"):
        assert gate.superseding.matches(gate.core, ASK, mention)

@pytest.mark.parametrize("reply", ["I approved the change and shipped it.",
    "No evidence of a regression; I decided to ship.",
    "Can you explain the exported repository variables branch reset?",
    "Please sign in to inspect exported repository variables branch reset.",
    "Do you want an explanation of the exported variables?",
    "I asked should I ship the exported variables change, then decided myself."])
def test_non_decision_requests_are_out_of_scope(related, gate, reply):
    assert gate._superseding_reply_hold("s1", reply, 0, 2) is None


@pytest.mark.parametrize("prefix", ["OK to spend on", "Is it fine to rent for", "Go ahead with",
    "Would you want me to deploy", "Please approve"])
def test_explicit_change_asks_without_denials_are_held(related, gate, prefix):
    assert gate._superseding_reply_hold("s1", prefix + " the stash guard exported variables change?", 0, 2)
