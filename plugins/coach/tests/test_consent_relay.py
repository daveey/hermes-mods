"""consent_relay (self-heal agent-2469206a31): a reply that asks the user to click OK on a macOS permission prompt AND
to unblock a card / reply "done" so the agent retries is a handoff, held deterministically before the judge.
run_in_user_terminal now waits `awaiting_consent` and opens its window by itself after the click, so the click is
the only human step. A one-step reply, the Kanban "Unblock = ..." meaning line and a postmortem quote pass."""
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("t_reply_check_consent", HERE / "reply_check.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

# Verbatim (trimmed) hub replies, @session:default/20261023_040944_37ae77, state.db msgs 821748 and 821814.
FIRST = (
    "So I handed the install to a Hermes chat on the Mac mini, which can open that window. The window didn't appear, "
    "though, because macOS is holding a privacy prompt on the mini: **\"Hermes Python\" wants access to control "
    "\"Terminal\"**. Agents can't approve those prompts.\n**Your one step, on the Mac mini:**\n"
    "1. Click **OK** on that prompt.\n"
    "2. Unblock the mini's card for the WWDR G3 install (t_01a1a4a80a2f8b72). Unblocking makes the mini chat reopen "
    "the password window. Archiving the card drops the install.\n")
REVISED = (
    "The Mac mini chat tried to open a password window for the installer. It failed before anything ran: the request "
    "to control Terminal timed out after 20 seconds.\n**Your one step, on the Mac mini:**\n"
    "1. Click **OK** on the prompt \"Hermes Python wants access to control Terminal\".\n"
    "2. If that prompt isn't showing, go to System Settings → Privacy & Security → Automation → Hermes Python and "
    "turn on **Terminal**.\n3. Reply \"done\" here.\n"
    "When you reply, I'll have the Mac mini chat run the installer again.")
ONE_STEP = (
    "macOS is asking on the mini whether Hermes Python may control Terminal. Click **OK** on that prompt; the "
    "password window then opens by itself. Type the mini's password there and the install finishes.")
CARD_MEANING = (
    "Click OK on the permission prompt on the mini, then type the password in the window that opens. "
    "Unblock = resume without the result; archive = abandon.")
POSTMORTEM = (
    "Root cause: the earlier reply said \"Click OK on that prompt, then unblock the mini's card so it retries\", which "
    "added a second step; the tool now waits awaiting_consent instead.")
# Real correction reply (msg 841102 shape): reports the old ask, does not repeat it.
CORRECTION = ("My last reply was wrong, so I've corrected it. It told you to unblock the card so an agent could open a "
              "password window after you click OK on the prompt; the window now opens by itself.")


def _no_judge(system, prompt):
    raise AssertionError("consent_relay must not need the LLM judge")


def test_real_replies_are_flagged_before_the_judge():
    for reply, needle in ((FIRST, "Unblock the mini's card"), (REVISED, "Reply \"done\"")):
        hit = rc.check(reply, "install the WWDR G3 cert on the mini", ["-> run_in_user_terminal(...)"], _no_judge)
        assert hit and hit["rule"] == "handoff" and hit["reason"] == "consent_relay" and hit["p"] == 1.0, hit
        assert needle in hit["evidence"]
        assert "awaiting_consent" in hit["nudge"] and "opens the password window by itself" in hit["nudge"]


def test_one_step_card_meaning_and_postmortem_pass():
    for text in (ONE_STEP, CARD_MEANING, POSTMORTEM, CORRECTION):
        assert rc.consent_relay(text) is None, text


def test_revised_reply_still_relaying_is_unresolved_without_judge():
    out = rc.handoff_outcome(REVISED, "install the cert", [], _no_judge)
    assert out["resolved"] is False and out["judged"] is False and out["src"] == "consent_relay"


def test_block_ask_with_unblock_retrigger_is_held_without_judge():
    hit = rc.block_handoff("Click OK on the Automation prompt on the mini, then unblock this card so I re-run it.",
                           "install the cert", [], _no_judge)
    assert hit and hit["src"] == "consent_relay" and "awaiting_consent" in hit["nudge"]
