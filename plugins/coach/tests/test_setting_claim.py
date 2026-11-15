"""setting_claim (self-heal agent-efa717f56e): a reply that says a setting, limit or flag is on/off/enabled/disabled
across several hosts, while this turn read no config, is held deterministically, before the judge.

Incident (hub session 20261110_185031_4f5f4b, 2026-11-15 05:49Z): a chat told David "The run limits are back on across
all nine machines." Another chat had switched kanban.llm_pacing off fleet-wide at 05:45Z, and an independent read at
05:46:55Z said enabled=False. The turn had read only the re-enable card's body and last comment, then commented on and
unblocked it; it restated that card's goal as live state. It corrected itself 9 minutes later. INCIDENT_REPLY is that
reply, verbatim.
"""
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("t_reply_check_setting_claim", HERE / "reply_check.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)
spec2 = importlib.util.spec_from_file_location("t_agent_rules_setting_claim", HERE / "agent_rules.py")
ar = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(ar)

INCIDENT_REPLY = (
    "The run limits are back on across all nine machines. The card that turned them back on (`t_01a1dcceb834776b`) "
    "asked you to choose \"retain Codex\" or \"change default to Anthropic\". That question came from a test I wrote "
    "into the card myself when OpenAI was out, so I've corrected it rather than asking you: unpinned cards keep "
    "running on OpenAI, and the routing doesn't change.\n\nOver two hours, 71 cards started, all 19 unpinned ones ran "
    "on OpenAI, and no card was held at zero runs. The card is back in the queue to finish. Two problems it found "
    "stay open, and it files a fix card for each:\n- the dispatcher logged 108 warnings that cards were stuck;\n- the "
    "hub's view of how much model allowance is left is 17 to 41 minutes out of date, against a 15-minute limit.")

# The incident turn's calls (msgs 1302084-1302089): the card's body and last comment, a comment, an unblock, a status
# read. No config read.
INCIDENT_LOG = [
    "-> terminal(cd ~; ~/.hermes/bin/hub-worker-exec worker-2 -- 'sqlite3 -readonly ~/.hermes/kanban.db \\\"select subs"
    "tr(body,1,1500) from tasks where id=\\\\\\\"t_01a1)",
    "-> tool_call({\"calls\": [{\"arguments\": {\"action\": \"comment\", \"body\": \"Correction from the hub owner chat, which w)",
    "-> terminal(hermes kanban unblock t_01a1dcceb834776b)",
    "-> terminal(cd ~; ~/.hermes/bin/hub-worker-exec worker-2 -- 'sqlite3 -readonly ~/.hermes/kanban.db \\\"select statu)",
]

# The correction turn's own reply (msg 1302105): present tense, but no fleet scope in the sentence.
CORRECTION_REPLY = (
    "I need to correct my last update: the run limits are switched off again. I said they were back on across all "
    "nine machines, but another chat turned them off at 05:45 UTC while it checked whether they were letting too few "
    "runs through.")

# A per-host config read that starts past the 140-char `->` cut.
CONFIG_CMD = ("cd ~; date -u +%H:%MZ; H=$(hostname); echo \"$H\"; ~/.hermes/bin/hub-worker-exec all -- 'true'; "
              "for h in hub; do echo $h; done; ~/.hermes/bin/hub-worker-exec all -- "
              "'hermes config get kanban.llm_pacing.enabled'")


def _no_judge(system, prompt):
    raise AssertionError("setting_claim must not need the LLM judge")


def _quiet_judge(system, prompt):
    return '{"handoff":0,"confirm":0,"unwatched":0,"report_only":0,"status_only":0,"evidence":""}'


def test_incident_reply_is_held_before_the_judge():
    hit = rc.check(INCIDENT_REPLY, "[chat-closeout] waiting on the user", INCIDENT_LOG, _no_judge,
                   only=["setting_claim"])
    assert hit and hit["rule"] == "setting_claim" and hit["p"] == 1.0
    assert "back on across all nine machines" in hit["evidence"]
    assert "hermes config get" in hit["nudge"]


def test_incident_reply_fires_in_the_default_rule_set():
    assert "setting_claim" in rc.DEFAULT_RULES and "setting_claim" in rc.settings()["rules"]
    assert "setting_claim" in rc.RULES and "setting_claim" in rc.REPLY_ONLY_RULES
    assert "setting_claim" in rc.NEW_CLAIM_RULES and "setting_claim" in rc.BACKTEST_RULES
    hit = rc.check(INCIDENT_REPLY, "[chat-closeout] waiting on the user", INCIDENT_LOG, _quiet_judge)
    assert hit and hit["rule"] == "setting_claim"


def test_config_reads_count():
    for line in ("-> terminal(hermes config get kanban.llm_pacing.enabled)",
                 "-> terminal(~/.hermes/bin/hub-worker-exec all -- 'grep -A3 llm_pacing ~/.hermes/config.yaml')",
                 "-> terminal(hermes config set kanban.llm_pacing.enabled true)",
                 "-> terminal(grep llm_pacing ~/code/hermes/shared-config.json)",
                 "-> terminal(hermes config show)",
                 "   <- SETTING read: hermes config get kanban.llm_pacing.enabled"):
        assert rc.setting_claim(INCIDENT_REPLY, INCIDENT_LOG + [line]) is None, line


def test_config_read_past_the_cut_counts_via_its_setting_read_line():
    lines = ar.setting_read_lines(CONFIG_CMD)
    assert lines and all(l.startswith("   <- SETTING read: ") for l in lines)
    cut = f"-> terminal({CONFIG_CMD[:ar.TURN_LOG_ARG_CAP]})"
    assert rc.setting_claim(INCIDENT_REPLY, INCIDENT_LOG + [cut]) is not None  # the cut line alone shows no read
    assert rc.setting_claim(INCIDENT_REPLY, INCIDENT_LOG + [cut] + lines) is None


def test_setting_read_line_patterns_match_reply_check():
    assert ar.SETTING_READ_RE.pattern == rc._SC_READ_RE.pattern


def test_other_fleet_setting_claims_are_held():
    for reply in ("Pacing is now off everywhere.",
                  "The governor stays disabled fleet-wide.",
                  "The kill switch is enabled on every host.",
                  "LLM pacing is enabled on the hub and all eight workers.",
                  "The concurrency caps are still in force across the workers."):
        assert rc.setting_claim(reply, INCIDENT_LOG), reply


def test_non_claims_pass():
    for reply in (CORRECTION_REPLY,
                  "Are the run limits back on across all nine machines?",
                  "Once pacing is enabled on all workers, the card re-measures.",
                  "If the limits are back on across the fleet, the card closes.",
                  "The run limits will be back on across all nine machines after the check.",
                  "The run limits were off across all hosts until 05:45 UTC.",
                  "Pacing is on on the hub.",
                  "The run limits aren't back on across the workers yet.",
                  "The fix is running on the hub and all eight workers.",
                  "The card that turns the limits back on everywhere is queued.",
                  "One option is yours: the spare account is at 24% weekly but is disabled on the hub, so workers "
                  "never get it.",
                  "setting_claim: the run limits are back on across all nine machines."):
        assert rc.setting_claim(reply, INCIDENT_LOG) is None, reply


def test_revision_that_keeps_the_claim_fails_the_outcome():
    out = rc.nudge_outcome("deploy_claim", INCIDENT_REPLY, "x", INCIDENT_LOG, _no_judge, extra_rules=("setting_claim",))
    assert out.get("new_claim", {}).get("rule") == "setting_claim"
