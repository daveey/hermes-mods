"""laptop_job (self-heal agent-60ca102b39): a reply proposing a recurring unattended job (cron, launchd, "every N
hours", a refresher/watcher) on the MacBook, laptop or mini, which sleep, is held deterministically before the judge.
Jobs that need a Mac's own state (Keychain, Chrome, Apple apps) and jobs on the hub pass."""
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("t_reply_check_laptop", HERE / "reply_check.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

# Verbatim reply 21:55 PT 2026-10-29, @session:default/20261028_221818_fdf44b (state.db msg 381513).
REAL_REPLY = (
    "`user/slack-config-softmax` is already stored. I just pulled it from token-broker and Slack confirmed it's the "
    "Softmax workspace, user daveey.\n\n"
    "I also checked your clipboard in case you meant something new. It's an 84-character line of text beginning "
    "\"Send\u2026\", not a token, so there was nothing else to store.\n\n"
    "One thing will catch up with it: the access token stops working about 12 hours after you generated it.\n"
    "- **What runs out:** after that, the refresh token can get a new pair once. Only your MacBook can save that new "
    "pair to token-broker. If anything else uses the refresh token, the stored copy dies.\n"
    "- **What I'd build:** a small job on the MacBook every 10 hours. It refreshes the pair, saves both new values, "
    "then checks with Slack that the saved token works. Say if you want it, and I'll build it.")


def _no_judge(system, prompt):
    raise AssertionError("laptop_job must not need the LLM judge")


def test_real_reply_is_flagged_before_the_judge():
    hit = rc.check(REAL_REPLY, "store the slack config token", ["-> terminal(command='token-broker get ...')"],
                   _no_judge)
    assert hit and hit["rule"] == "laptop_job" and hit["p"] == 1.0
    assert "hermes-hub" in hit["nudge"] and "grant" in hit["nudge"]
    assert "MacBook" in hit["evidence"]


def test_other_laptop_job_wordings_are_flagged():
    for text in ("I'll add a launchd job on your MacBook that refreshes the token hourly.",
                 "Plan: a cron on the mini every 6 hours re-checks the feed.",
                 "Want me to set up a nightly watcher on the laptop?",
                 "I can schedule it on the MBP to run every 30 minutes."):
        assert rc.laptop_job(text), text


def test_hub_cron_is_not_flagged():
    for text in ("I'll add a cron on hermes-hub every 10 hours that refreshes the pair.",
                 "What I'd build: a hub cron every 10 hours; the MacBook only does the one-time grant.",
                 "Set up an hourly job on the hub; a grant card covers the write the MacBook can do today."):
        assert rc.laptop_job(text) is None, text


def test_mac_only_job_justified_by_local_state_is_not_flagged():
    for text in ("I'll add a launchd job on the MacBook every hour, since it reads the login Keychain.",
                 "A cron on the MacBook every 10 minutes is needed because it drives David's signed-in Chrome."):
        assert rc.laptop_job(text) is None, text


def test_mention_only_is_not_flagged():
    for text in ("The old refresh job ran on the MacBook every 10 hours until it slept through a rotation.",
                 "The MacBook's hourly hermes-sync launchd job pushed the commit.",
                 "laptop_job now flags a cron on the MacBook every hour.",
                 "I'll add an hourly cron that scores replies with gpt-4o-mini."):
        assert rc.laptop_job(text) is None, text


def test_backtest_false_positives_are_not_flagged():
    # hub state.db, 30 days of replies (2026-10-30 backtest): mentions, checks and negations, not proposals
    for text in ("A14: the location commits add no LaunchAgent, cron or MacBook job.",
                 "Add a watcher that opens a fix card when the MacBook Desktop backend starts timing out.",
                 "I'll check the Mac snapshot, the posting cron, and whether the tweet is live on X.",
                 "I've parked the card to resume when CI run 40786412758 finishes (checked every 5 minutes), then "
                 "merge on green and set up the MacBook live check.",
                 "I'll check the request state machine and whether the MacBook poller is applying them.",
                 "A MacBook request to create a card has been stuck since 2026-10-28, still repeating every minute.",
                 "Live run on the mini: scheduled run 39e7cf78 finished ok with exit 0.",
                 "What happened: on the MacBook, the session-idle-check cron run started at 18:01 PT.",
                 "The Risk ledger push cron job works again: its first run on the MacBook with the fix succeeded."):
        assert rc.laptop_job(text) is None, text


# MacBook reply ~19:46 PST 2026-11-01 (@session:default/20261026_235722_bef6c6), one product name removed: it lists the
# producers existing waits already have. "move them" is not a creation verb, so nothing here proposes a job.
STATUS_LIST = ("The 9 still-open cards that hit this dead end are all waiting on something that will move them: a "
               "running card, the hand-off watcher, the 22:00 quota reset on the mini, or an ask you already have.")


def test_move_or_put_without_a_mac_destination_is_not_a_proposal():
    for text in (STATUS_LIST,
                 "Nothing will move these cards, including the hourly reset on the mini.",
                 "Put differently, the watcher on the MacBook already runs every hour."):
        assert rc.laptop_job(text) is None, text


def test_move_or_put_onto_a_mac_is_still_a_proposal():
    for text in ("Put the hourly refresher on the mini.",
                 "I'd move the watcher onto the MacBook and run it every hour.",
                 "Let's put the token refresher on your MacBook every 10 hours."):
        assert rc.laptop_job(text), text


def test_quoted_or_code_mention_is_not_flagged():
    for text in ("You said \"a small job on the MacBook every 10 hours\" won't work, so it runs on the hub.",
                 "The skill used to say `I'll add a cron on the MacBook every hour`.",
                 "> I'd build a small job on the MacBook every 10 hours."):
        assert rc.laptop_job(text) is None, text


def test_rule_is_on_by_default():
    assert "laptop_job" in rc.RULES and "laptop_job" in rc.DEFAULT_RULES
    assert "laptop_job" in rc.settings()["rules"]
