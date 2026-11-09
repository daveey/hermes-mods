"""skipped_live (self-heal agent-8a0b79e7c4): a final reply admitting it never checked live state ("I didn't check the
live state: this machine has no AWS access to the <X> account") while the turn tried no other live route (browser,
`gh run` logs, a web fetch) is nudged deterministically, before the judge.

REAL_REPLY / REAL_LOG are the real reply and tool log of the incident turn (hub state.db row 708456), with the
product, repo and people names replaced (this repo is public). The structure and the admission sentence are verbatim.
"""
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("t_reply_check_skipped_live", HERE / "reply_check.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

REAL_REPLY = (
    "The \"Hiring: coming soon\" message comes from a deliberate allowlist, and opening it up needs your approval "
    "first.\n\n**Plan** (card `t_01a19e4974912dcf`):\n1. Every new hire starts on the pool automatically.\n"
    "2. Move every existing owner onto the pool, and run the runbook's per-owner checks.\n"
    "3. Let anyone with a company email hire. Keep the current limits: 25 live assistants in total.\n\n"
    "**Decision for you:** on the card, choose **Go**, **Just add Sam now** or **Hold**. I recommend **Go**.\n\n"
    "I didn't check the live state: this machine has no AWS access to the Acme account. So I don't know yet how "
    "many owners already use the pool or how close we are to the 25-assistant limit. That gets checked in step 2.")

# The incident turn's tool log, as agent_rules.observe writes it: repo reads, a `gh pr list`, an ~/.aws probe and two
# kanban_create calls. No browser, no `gh run`, no web fetch.
REAL_LOG = [
    "-> skill_view(acme-operations)",
    "-> terminal(ls ~/code | head -50; cd ~/code/acme 2>/dev/null && git fetch -q origin && git status -sb | head -3)",
    "-> terminal(cd ~/code && rmdir acme && ~/.local/bin/token-broker exec --scope user/github -- gh repo clone x/acme)",
    "-> terminal(cd ~/code/acme && sed -n 1,120p docs/runbooks/assistants.md; grep -n \"ALLOWLIST\" apps/web/src/lib)",
    "-> kanban_list({\"limit\": 200})",
    "-> execute_code(import json\nd=json.load(open('/tmp/spill.txt'))\nfor t in d['tasks']:\n    s=t['title)",
    "-> terminal(cd ~/code/acme && TB=~/.local/bin/token-broker; $TB exec --scope user/github -- sh -c 'gh pr list -R x/acme)",
    "-> terminal(ls ~/.aws 2>&1; grep -n \"^\\[\" ~/.aws/config 2>/dev/null | head; ~/.local/bin/token-broker scopes)",
    "-> terminal(cd ~/code/acme && git grep -n \"GLOBAL_CAP\\s*=\\|MAX_OWNERS\" -- apps/web/src scripts/lib)",
    "-> kanban_create({\"title\": \"Acme: open assistant hiring to every employee\", \"assignee\": \"default\"})",
]


def _no_judge(system, prompt):
    raise AssertionError("skipped_live must not need the LLM judge")


def test_real_reply_with_terminal_only_log_fires_before_the_judge():
    hit = rc.check(REAL_REPLY, "in acme", REAL_LOG, _no_judge, only=["skipped_live"])
    assert hit and hit["rule"] == "skipped_live" and hit["p"] == 1.0
    assert "no AWS access to" in hit["evidence"] or "didn't check the live state" in hit["evidence"]
    assert "no-profile" in hit["nudge"] and "tried" in hit["nudge"]


def test_real_reply_fires_in_the_default_rule_set():
    assert "skipped_live" in rc.DEFAULT_RULES and "skipped_live" in rc.settings()["rules"]
    hit = rc.check(REAL_REPLY, "in acme", REAL_LOG, lambda s, p: '{"handoff":0,"confirm":0,"unwatched":0,'
                   '"report_only":0,"status_only":0,"evidence":""}')
    assert hit and hit["rule"] == "skipped_live"


def test_same_reply_after_a_browser_call_does_not_fire():
    log = REAL_LOG + ["-> browser_exec(# Read the assistants console\nnew_tab('https://console.example.com/'))"]
    assert rc.skipped_live(REAL_REPLY, log) is None
    deferred = REAL_LOG + ["-> tool_call({\"calls\": [{\"name\": \"mcp__chrome_devtools__navigate_page\"}]})"]
    assert rc.skipped_live(REAL_REPLY, deferred) is None


def test_gh_run_logs_or_a_web_fetch_count_as_a_live_route():
    assert rc.skipped_live(REAL_REPLY, REAL_LOG + ["-> terminal(gh run view 123 --log -R x/acme | tail)"]) is None
    assert rc.skipped_live(REAL_REPLY, REAL_LOG + ["-> web_extract(https://status.example.com)"]) is None
    assert rc.skipped_live(REAL_REPLY, REAL_LOG + ["-> terminal(curl -s https://api.example.com/v1/state)"]) is None


def test_gh_pr_list_is_not_a_live_state_route():
    assert rc.skipped_live(REAL_REPLY, ["-> terminal(gh pr list -R x/acme)"])


def test_phrase_in_backticks_or_quotes_does_not_fire():
    assert rc.skipped_live("The old reply said `I didn't check the live state: no AWS access to the account`.",
                           REAL_LOG) is None
    assert rc.skipped_live('The postmortem quotes "this machine has no AWS access to the Acme account".',
                           REAL_LOG) is None
    assert rc.skipped_live("> I didn't check the live state.\n\nFixed the rule.", REAL_LOG) is None


def test_reply_naming_the_routes_it_tried_does_not_fire():
    revised = REAL_REPLY + ("\n\nI tried the console in the hub browser (sign-in page timed out) and `gh run view` "
                            "for the deploy job (no runs in 30 days).")
    assert rc.skipped_live(revised, REAL_LOG) is None


def test_other_phrasings_fire():
    for s in ("I couldn't verify the current state of the cluster, so this plan assumes the defaults.",
              "I haven't checked the live config yet; the numbers come from the repo.",
              "The hub has no access to the prod account, so I left the counts out."):
        assert rc.skipped_live(s, REAL_LOG), s


def test_unrelated_replies_do_not_fire():
    for s in ("Checked the live state in the console: 1 owner, already on the pool.",
              "The sandbox has no network access to the metadata endpoint, as the runbook requires.",
              "No access token is stored in the repo.",
              "Mentioned skipped_live in the README.",
              # backtest false positives (14 days of hub replies, 2026-11-09), names replaced
              "Sam having no access to the private repo no longer blocks the internal work.",
              "The service has no access to the AWS account the hub is in.",
              "I changed nothing: no gateway restart, no config change, no access to the boards.",
              "PR 104 is still unmerged and CI hasn't finished, so I haven't checked the live count yet.",
              "The hub has no access to the laptop, so I handed the live run to a laptop card, t_01a186967dbdd6b5."):
        assert rc.skipped_live(s, REAL_LOG) is None, s


def test_rule_lists_stay_in_step():
    assert "skipped_live" in rc.RULES and "skipped_live" in rc.BACKTEST_RULES
    src = (HERE / "signals.py").read_text() + (HERE / "self_heal.py").read_text()
    assert src.count('"skipped_live"') >= 2
