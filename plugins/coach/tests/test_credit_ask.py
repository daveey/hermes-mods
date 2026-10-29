"""credit_ask (self-heal agent-fe419d4901): a reply asking the user to buy API credit while the turn never tried the
Hermes account pool is held deterministically, before the judge."""
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("t_reply_check_credit", HERE / "reply_check.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

# Verbatim tail of the 01:47 PT reply in @session:default/20261027_152719_2b256c (msg 661112, coach digest).
REAL_REPLY = ("**Still needs you:** v11 is still waiting on API credit. At 01:34 PT, Anthropic still said \"credit "
              "balance is too low\" and OpenAI \"no credits remaining\". Add about $250 to each, or reply "
              "\"OpenRouter\". The eval restarts on its own within 15 minutes of both keys working.\n"
              "- https://console.anthropic.com/settings/billing\n"
              "- https://platform.openai.com/settings/organization/billing")


def _no_judge(system, prompt):
    raise AssertionError("credit_ask must not need the LLM judge")


def test_real_reply_is_flagged_before_the_judge():
    hit = rc.check(REAL_REPLY, "hows it looking", ["-> terminal(command='bash check_credits.sh')"], _no_judge)
    assert hit and hit["rule"] == "credit_ask" and hit["p"] == 1.0
    assert "hermes-pool-llm" in hit["nudge"]


def test_reply_passes_when_the_turn_tried_the_pool():
    log = ["-> terminal(command='~/.hermes/plugins/pool-llm/bin/hermes-pool-llm check')"]
    assert rc.credit_ask(REAL_REPLY, log) is None


def test_plain_status_mentioning_credit_is_not_flagged():
    assert rc.credit_ask("The eval now runs on the account pool; no credit was added.", []) is None
    assert rc.credit_ask("Moved the judges to hermes-pool-llm serve; GitHub billing is unrelated.", []) is None


def test_rule_is_on_by_default():
    assert "credit_ask" in rc.DEFAULT_RULES and "credit_ask" in rc.settings()["rules"]


# Live false positive (hermes chat -q 20261029_173219_f18d43): a reply echoing a terminal result verbatim repeated
# pool-llm's hint, whose old wording "Don't ask the user to buy credit" matched.
ECHOED_HINT = ("openai.RateLimitError: Error code: 429 - You have no credits remaining\n\npool-llm: this is a paid API "
               "key out of credit. Don't ask the user to buy credit: run the call on the Hermes subscription account pool "
               "instead (Claude Max + ChatGPT accounts, rotates on limits).")


def test_echoed_pool_llm_hint_is_not_an_ask():
    assert rc.credit_ask(ECHOED_HINT, ["-> terminal(command=\"echo '...no credits remaining'\")"]) is None


def test_ask_in_code_or_quote_is_not_an_ask_but_plain_ask_still_is():
    assert rc.credit_ask("The old skill said `add credits to the API org`.", []) is None
    assert rc.credit_ask("Please add credits to the OpenAI API org so the eval can continue.", [])


# Live false positive (2026-10-29): a status reply saying credit is NOT needed was held as a credit ask.
NEGATED_REPLY = ("- So no credit top-up is needed. Claude and GPT run on our Hermes accounts, and embeddings go "
                 "through the same pool to OpenRouter for cents.")


def test_reply_saying_credit_is_not_needed_is_not_an_ask():
    assert rc.credit_ask(NEGATED_REPLY, ["-> terminal(command='pytest -q tests')"]) is None
    for text in ("No credit top-up needed: the eval runs on the account pool.",
                 "We don't need to add credits; the pool covers it.",
                 "No need to buy API credits for this run.",
                 "A credit top-up isn't needed.",
                 "API credit: no top-up is needed."):
        assert rc.credit_ask(text, []) is None, text


def test_negation_guard_keeps_real_asks():
    for text in ("No credits left, so a top-up is needed.",
                 "No need to wait; add $250 of credit to each.",
                 "You don't need to do anything but add $250 of credit.",
                 "No API credit is needed for Claude, but add $250 of OpenAI credit."):
        assert rc.credit_ask(text, []), text


# Live false positive (Inbox zero-junk plan step 7): the GitHub Actions billing watchdog reply said the spending-limit
# raise stays a typed approval card. The account pool can't pay non-LLM bills, so only LLM provider credit counts.
GH_ACTIONS_REPLY = ("Step 7: the GitHub Actions billing watchdog now posts when the monthly Actions minutes run low; "
                    "only 'raise the spending limit / pay' stays a typed approval card.")


def test_github_actions_billing_is_not_an_llm_credit_ask():
    assert rc.credit_ask(GH_ACTIONS_REPLY, []) is None
    assert rc.credit_ask("GitHub Actions is out of minutes: raise the spending limit on the billing page.", []) is None


def test_other_non_llm_billing_is_not_an_llm_credit_ask():
    for r in ("Please add $50 of credit to the Vast.ai account so the GPU rental can continue.",
              "Raise the AWS budget to $500 so the EC2 job keeps running.",
              "Top up the Cloudflare balance before Friday."):
        assert rc.credit_ask(r, []) is None, r


def test_llm_credit_ask_still_fires():
    assert rc.credit_ask("Please add Anthropic API credit so the eval can continue.", [])
    assert rc.credit_ask("OpenAI returned insufficient_quota. Add $100 of credit and I'll rerun it.", [])
    # an LLM ask still fires when another paragraph mentions GitHub
    assert rc.credit_ask("CI on GitHub is green.\n\nAdd $250 of Anthropic credit so the eval can finish.", [])


# Review round 1: a bare provider name is not billing context. A reply that mentions GitHub elsewhere still asks for
# LLM credit; Claude Code named next to a GitHub Actions spending limit, or an AWS API key, is not an LLM credit ask.
def test_bare_github_mention_does_not_silence_a_credit_ask():
    for r in ("Pushed to GitHub. Please add $250 of credit to continue.",
              "I opened PR #12 on GitHub.\n\nThe eval stopped: add $250 of credit and I'll rerun it.",
              "Opened the PR on GitHub. Add $250 of Anthropic credit so the eval can finish."):
        assert rc.credit_ask(r, []), r


def test_bare_llm_name_does_not_turn_non_llm_billing_into_an_ask():
    for r in ("The GitHub Actions run failed because Claude Code couldn't start; raise the spending limit for Actions.",
              "I rotated the AWS API key. Add $20 of credit to AWS so the EC2 job keeps running.",
              "Add $20 of credit to AWS; the AWS API key is in the vault."):
        assert rc.credit_ask(r, []) is None, r


# Live false positive after ccd7722: a reply DESCRIBING the rule (quoted example + "still does") was held as an ask.
META_REPLY = ("Fixed: \"Raise the GitHub Actions spending limit\" no longer sets it off, while a real ask to add "
              "Anthropic or OpenAI credit still does.")


def test_quoted_or_reported_mention_is_not_an_ask():
    assert rc.credit_ask(META_REPLY, []) is None
    for r in ("Added a test: \"Please add $250 of Anthropic credit\" is held before the judge.",
              "The old card said \u201cadd $100 of OpenAI API credit\u201d.",
              "The credit_ask rule caught \"add Anthropic credit\" in my last reply.",
              "That was a false alarm: add Anthropic API credit only matched the detector's regex.",
              "Asking David to add $250 of OpenAI credit no longer fires once the pool was tried."):
        assert rc.credit_ask(r, []) is None, r


def test_real_asks_still_fire_next_to_quotes_and_meta_wording():
    assert rc.credit_ask("Please add Anthropic API credit, the credit balance is too low.", [])
    assert rc.credit_ask(REAL_REPLY, [])  # quotes elsewhere on the line do not cover the ask
    assert rc.credit_ask("The eval still does nothing. Add $250 of Anthropic credit so it can finish.", [])
    assert rc.credit_ask("Anthropic says \"credit balance is too low\", so please add $100 of credit.", [])
    assert rc.credit_ask("It still fails: add $100 of OpenAI API credit and I'll rerun it.", [])
    # GitHub Actions billing stays out
    assert rc.credit_ask("Raise the GitHub Actions spending limit to $50 so CI can run.", []) is None


# Review round 1 (a6a17e4): the meta guard was loose enough to swallow real asks.
MUST_FIRE = ("The pool still does nothing; please add $50 of Anthropic credit.",
             "The API still fires 429s, so please top up OpenAI credit.",
             "Please add Anthropic credit so the detector job can run.",
             "The pool still does nothing; add $50 of Anthropic credit.",
             "The eval still fires errors, add $100 of OpenAI API credit.")


def test_meta_guard_keeps_direct_asks():
    for r in MUST_FIRE:
        assert rc.credit_ask(r, []), r
    # the reported forms stay silent
    assert rc.credit_ask("The rule matched: add $250 of Anthropic credit was a false positive.", []) is None
    assert rc.credit_ask("An ask to add $250 of Anthropic credit still fires, as intended.", []) is None


def test_pool_llm_mirror_matches_coach():
    """pool-llm/hooks.py carries a copy of this logic; the two must not drift."""
    spec2 = importlib.util.spec_from_file_location("t_pool_llm_hooks_mirror", HERE.parent / "pool-llm" / "hooks.py")
    h = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(h)
    for name in ("CREDIT_ASK_RE", "LLM_CREDIT_RE", "NON_LLM_BILLING_RE", "_SENTENCE_END", "_QUOTED_RE", "_META_RE",
                 "_CLAUSE_CUT", "_DIRECT_RE"):
        a, b = getattr(rc, name), getattr(h, name)
        assert (a.pattern, a.flags) == (b.pattern, b.flags), name
    import inspect
    for fn in ("_nearest", "_about_llm_credit", "_mentioned_not_asked", "_is_llm_credit_ask"):
        assert inspect.getsource(getattr(rc, fn)) == inspect.getsource(getattr(h, fn)), fn
    for r in (META_REPLY, "Please add Anthropic API credit, the credit balance is too low.",
              "Raise the GitHub Actions spending limit to $50 so CI can run.") + MUST_FIRE:
        assert bool(rc.credit_ask(r, [])) == bool(h.credit_ask("kanban_block", {"reason": r})), r
