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
