"""ask_context_hold + acceptance_met (ask_context.py), on the three hub asks behind t_01a21f5e47abf118.

* t_01a21bb1169deea2 (unforged CI, PR #13): cites a PR with no URL, and its recommended option's cost is "additional
  agent work" with no size. Held.
* t_01a211fcd5927efb (iPhone qualification): device, steps, costs and both buttons. Passes.
* t_01a21ef258f83d8d (billing watchdog): its acceptance "verify the affected workflows or document the external
  hold with evidence" needs no ticket read. Held as acceptance_met once its money wording is removed; verbatim, its
  "No payment or limit increase is requested" / "Billing shows $0" fail closed and the (well-formed) ask is filed.
"""
import copy
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("ask_gate_ask_context_under_test", HERE.parent / "ask_context.py")
ac = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ac
_spec.loader.exec_module(ac)


def fixture(name):
    return json.loads((HERE / "fixtures" / f"ask_context_{name}.json").read_text())


UNFORGED = fixture("t_01a21bb1169deea2")
IPHONE = fixture("t_01a211fcd5927efb")
BILLING = fixture("billing_t_01a21ef258f83d8d")


def _without_money_words(hr):
    """The billing card's ticket-read ask with its money wording removed (its money mentions fail closed)."""
    hr = copy.deepcopy(hr)
    hr["action"] = hr["action"].replace(" No payment or limit increase is requested.", "")
    hr["scope"] = (hr["scope"].replace("Billing shows $0 net charges and hosted tests passed.", "Hosted tests passed.")
                   .replace("no spending, ", ""))
    assert not ac._limit_named("\n".join(hr[k] for k in ("action", "why_human", "scope")))
    return hr


BILLING_TICKET = _without_money_words(BILLING["human_request"])


@pytest.fixture(autouse=True)
def _ask_context_on(monkeypatch):
    monkeypatch.setenv(ac.ENV_SWITCH, "1")


def test_the_unforged_ask_is_refused_for_missing_context():
    hit = ac.hit(UNFORGED["human_request"], UNFORGED["body"])
    assert hit and hit["rule"] == "ask_context_hold"
    assert any(m.startswith("where: PR/issue #13") for m in hit["missing"])
    assert any("recommended option" in m and "no size or cost" in m for m in hit["missing"])
    assert "Missing:" in hit["message"] and "Unblock and Archive" in hit["message"]


def test_the_unforged_ask_passes_once_it_carries_the_url_and_the_size():
    hr = copy.deepcopy(UNFORGED["human_request"])
    hr["scope"] = hr["scope"].replace(
        "rerun CI; additional agent work", "rerun CI on https://github.com/softmax-universe/unforged/pull/13; about "
        "two agent runs (under $5)")
    assert ac.hit(hr, UNFORGED["body"]) is None


def test_the_iphone_ask_passes():
    assert ac.missing(IPHONE["human_request"]) == []
    assert ac.hit(IPHONE["human_request"], IPHONE["body"]) is None


def test_the_billing_ask_is_refused_because_its_acceptance_is_met_without_it():
    hit = ac.hit(BILLING_TICKET, BILLING["body"])
    assert hit and hit["rule"] == "acceptance_met"
    assert hit["message"].startswith("ask-gate: acceptance met without this; complete instead.")
    assert "document the external hold with evidence" in hit["message"]


def test_the_verbatim_billing_ask_fails_closed_on_its_money_words():
    """Replan (owning chat, 2026-11-28): a money mention, even negated, is never a reason to skip the full ask."""
    assert "No payment or limit increase is requested" in BILLING["human_request"]["action"]
    assert ac.acceptance_met(BILLING["human_request"], BILLING["body"]) is None
    assert ac.hit(BILLING["human_request"], BILLING["body"]) is None  # well-formed: context, place, costs, buttons


def test_an_acceptance_alternative_does_not_refuse_a_spend_or_permission_ask():
    hr = dict(BILLING["human_request"], why_human="Only you can approve paying $50 to raise the spending limit.")
    assert ac.acceptance_met(hr, BILLING["body"]) is None


@pytest.mark.parametrize("text", [
    "Approve a one-time $50 payment to GitHub for this month's Actions overage.",
    "Grant the worker read access to the billing API credentials.",
    "Approve deleting the old hosted runner group (irreversible).",
])
def test_explicit_money_permission_or_destructive_asks_are_exempt(text):
    """Review 6094541114 P2: a concrete one-time $50 payment approval was not exempted."""
    hr = dict(BILLING["human_request"], action=text + " Unblock = do it; archive = drop it.")
    assert ac.acceptance_met(hr, BILLING["body"]) is None


@pytest.mark.parametrize("why_human, action", [
    ("Only you can authorize this action", "No permission change is needed, but approve a one-time $50 payment"),
    ("I am not authorized to pay $50 without your approval", "Approve it in the GitHub billing page"),
])
def test_negation_removal_keeps_an_affirmative_payment(why_human, action):
    """Review 6094667454 P2: negation removal swallowed a later affirmative $50 approval and a human-only payment."""
    hr = dict(BILLING_TICKET, why_human=why_human, scope="Only this operation",
              action=action + ". Unblock = do it; archive = drop it.")
    assert ac.acceptance_met(hr, BILLING["body"]) is None
    assert ac.hit(hr, BILLING["body"]) is None or ac.hit(hr, BILLING["body"])["rule"] != "acceptance_met"


# Every case a reviewer raised on PR #549 (reviews 6094541114, 6094667454, 6094768133, 6094853169, 6094980043),
# whichever way the earlier heuristics wanted it. Fail closed: each mentions money/payment/spend/billing/limits, so
# none may be short-circuited by acceptance_met -- each takes the full context/steps/choices check instead.
PAST_REVIEWER_CASES = [
    # 6094541114
    "Approve a one-time $50 payment to GitHub for this month's Actions overage",
    # 6094667454: negated, and read-only subject matter
    "No payment or limit increase is requested", "No permission change is needed, but approve a one-time $50 payment",
    "I am not authorized to pay $50 without your approval", "Approve it in the GitHub billing page",
    "Open the billing support ticket and supply its latest status", "Read the subscription status and paste it here",
    "Send me this month's budget report", "Only you can read the invoice status through your signed-in browser",
    # 6094768133: money changes without a $ amount, and read-only/negated text
    "Pay the March invoice", "Raise the monthly budget to cover the overage", "Cancel the Superhuman subscription",
    "Confirm the refund for order 4411", "Process the refund for the duplicate order",
    "Refund the customer for the duplicate order", "Settle the outstanding invoice",
    "Submit the invoice to the vendor portal", "Confirm the subscription renewal",
    "Okay the budget increase for this sprint", "Sign off on the budget increase", "Accept the new subscription terms",
    "Start by reading the invoice status", "Add the invoice link to this card", "Look into the issue with the invoice",
    "Nothing to pay here", "You won't need to pay anything",
    # 6094853169: _replan spend classes, verb order, URLs, 'issue', and the declined FPs
    "Read the request for extra budget and reply yes or no", "Review the request for more budget",
    "See the request for additional budget", "Check the extra budget request, then approve it",
    "Review the new budget request and sign it", "Check the order history and refund the customer",
    "Look up order 4411 and refund it", "Review the timesheet and bill the client",
    "Look at the invoice, then approve it", "Open the subscription page and cancel it",
    "Read the status at https://github.com/settings/billing/invoices/2026-11 and reply",
    "Check the invoice issue on GitHub", "Ask Bill to confirm the rollout", "Read the subscription terms",
    "There is no invoice involved",
    # 6094980043: inflections and currencies (substring stems, not word lists)
    "We are rate-limited; approve raising the tier", "The API usage is capped; approve lifting it",
    "Approve the unlimited plan", "Raise the concurrency limitation", "Approve €50 for the GPU rental",
    "Approve a £20 top-up", "Approve USD 40 for the run", "Approve the 5k expense", "Reimburse me for the GPU hours",
    # 6095069519: stems the substring rewrite dropped (regression), then amounts, money nouns, rent/lease, tier/seat
    # and limit synonyms
    "Buy a domain for the site", "Approve buying 2 more seats", "I bought the wrong tier; approve the swap",
    "Approve 40 dollars", "Approve the card on file", "Approve the plan upgrade", "Approve the plan change",
    "Approve ten bucks for the run", "Approve 500 cents", "Approve a 30 yen run", "Approve 200 CAD", "Approve 300 AUD",
    "Approve 1000 INR", "Approve 50 CHF", "Approve ₩5000", "Approve the 0.5 BTC", "Approve sending 0.1 ETH",
    "Approve funding the account", "Add funds to the account", "Wire the deposit", "Approve the deposit",
    "Approve the donation", "Approve the tip", "Approve the order of 2 GPUs", "Check out the cart on Amazon",
    "Approve the Stripe checkout", "Approve the Venmo", "Approve the tax", "Approve the fine", "Approve the dues",
    "Approve the stipend", "Approve the salary", "Approve the bonus", "Approve the 5k outlay", "Approve the overrun",
    "Rent a GPU box for a day", "Approve renting 8 H100s", "Approve the rental", "Lease an extra H100",
    "Approve the H100 reservation", "Approve the reserved instance", "Upgrade the plan to Pro",
    "Switch to the Team tier", "Approve the Pro tier", "Approve upgrading to the premium tier",
    "Approve the extra seat", "Approve the trial conversion", "Raise the ceiling", "Raise the max",
    "Raise the maximum concurrency", "Raise the rate threshold", "Approve exceeding the threshold",
    "Increase the allowance", "Lift the throttle", "Approve the throttle increase",
    # 6094911651 (at c8189fca, superseded by the fail-closed replan but still a reviewer round): issue + pronoun,
    # numbered-step and semicolon asks, verbs missing after a reading word, and the noted safe-direction FPs
    "Review the refunds and issue them", "Check the refund and issue it", "Review the invoices and issue them",
    "Look at the refund and issue one", "Check the refund request and issue it to the customer",
    "1) Open the invoice at https://vendor.example.com/inv/42 in Chrome. 2) Approve it",
    "1) Open the subscription page in Chrome. 2) Cancel it", "1) Check the budget request. 2) Sign off on it",
    "Look at the subscription page; cancel it", "Read the invoice. Then approve it",
    "Read the invoice and wire the money to the vendor", "Review the invoice and transfer the funds",
    "Read the bill and send the vendor the money", "Check the budget and top it up",
    "Open the billing portal and top up the account", "Review the invoice and void it",
    "Open the subscription page and unsubscribe", "Review the budget and bump it to 500",
    "Look at the budget and add 200 to it", "Review the invoice status and mark it paid",
    "Check the subscription and switch it to the annual plan", "Check the subscription and end it",
    "Check the invoice status and confirm it looks right", "Review the budget report and accept the PR",
    "Open the subscription page and process the logs", "Check the invoice email and reply okay",
    "Read the budget summary and submit your notes", "Check the invoice link and raise any concerns",
    "Read the invoice and tell me if the change log is right",
]


@pytest.mark.parametrize("text", PAST_REVIEWER_CASES)
def test_every_past_reviewer_case_fails_closed(text):
    for field in ("action", "why_human", "scope"):
        hr = dict(BILLING_TICKET)
        hr[field] = text + ". " + hr[field]
        assert ac.acceptance_met(hr, BILLING["body"]) is None, field
        hit = ac.hit(hr, BILLING["body"])
        assert hit is None or hit["rule"] == "ask_context_hold"
    # Without the mention, the same ask is refused as acceptance_met: the mention alone is what keeps it.
    assert ac.acceptance_met(BILLING_TICKET, BILLING["body"]) is not None


# Every ask_context.py this PR shipped before the current head, frozen as text (tests/fixtures/ask_context_history).
HISTORY = ["e4eaafe4", "856f371f", "92428da5", "178462fe", "c8189fca", "0f4365e7", "1e7f0c2b"]


def _historical(sha):
    """Load a frozen ask_context.py; __file__ points at the live plugin dir so it finds replan.py like it did."""
    src = (HERE / "fixtures" / "ask_context_history" / f"ask_context_{sha}.py.txt").read_text()
    mod = types.ModuleType(f"ask_context_{sha}")
    mod.__file__ = str(HERE.parent / "ask_context.py")
    sys.modules[mod.__name__] = mod
    exec(compile(src, mod.__file__ + f"@{sha}", "exec"), mod.__dict__)
    return mod


def _names_a_limit(mod, text):
    if hasattr(mod, "_limit_named"):
        return mod._limit_named(text)
    return mod._replan.limit_named(text) in ("spend", "permission", "safety")  # e4eaafe4: _replan only


@pytest.mark.parametrize("sha", HISTORY)
def test_the_matcher_is_a_superset_of_every_earlier_version(sha):
    """Owning chat 02:05Z: the check catches the union of every term ever matched; no term is ever removed."""
    old = _historical(sha)
    probes = PAST_REVIEWER_CASES + [BILLING["human_request"][k] for k in ("action", "why_human", "scope")]
    lost = [t for t in probes if _names_a_limit(old, t) and not ac._limit_named(t)]
    assert lost == [], f"{sha} named these as money/limit asks but the head does not: {lost}"
    # And every reviewer case is named at the head, whatever the old version said.
    assert [t for t in PAST_REVIEWER_CASES if not ac._limit_named(t)] == []


@pytest.mark.parametrize("leg,text", [
    # Review 6095147187 (non-blocking): pin each leg of _limit_named with a probe only that leg catches, so dropping
    # either leg turns this red.
    ("words", "Approve invoicing the client"),
    ("replan", "Approve broader access for the bot"),
    ("replan", "Ack the security outage"),
    ("replan", "Accept the data loss"),
])
def test_each_leg_of_the_matcher_is_pinned_by_its_own_probe(leg, text):
    words = bool(ac._LIMIT_WORDS.search(text))
    stems = bool(ac._LIMIT_STEMS.search(text))
    replan = ac._replan.limit_named(text) in ("spend", "permission", "safety")
    assert ac._limit_named(text)
    assert (words, stems, replan) == (leg == "words", False, leg == "replan"), (words, stems, replan)


@pytest.mark.parametrize("text", [
    "Pay the March invoice", "Raise the monthly budget to cover the overage", "Cancel the Superhuman subscription",
    # Review 6094768133 P2: money changes without a $ amount or a listed verb before the noun.
    "Confirm the refund for order 4411", "Process the refund for the duplicate order",
    "Refund the customer for the duplicate order", "Settle the outstanding invoice",
    "Submit the invoice to the vendor portal", "Confirm the subscription renewal",
    "Okay the budget increase for this sprint", "Sign off on the budget increase",
    "Accept the new subscription terms",
    # Review 6094853169 P2: _replan's own spend classes, refund/bill as the verb after a reading word, and a change
    # verb further from the noun.
    "Read the request for extra budget and reply yes or no", "Review the request for more budget",
    "See the request for additional budget", "Check the extra budget request, then approve it",
    "Review the new budget request and sign it", "Check the order history and refund the customer",
    "Look up order 4411 and refund it", "Review the timesheet and bill the client",
    "Look at the invoice, then approve it", "Open the subscription page and cancel it",
])
def test_a_financial_change_is_still_exempt(text):
    hr = dict(BILLING["human_request"], action=text + ". Unblock = do it; archive = drop it.")
    assert ac.acceptance_met(hr, BILLING["body"]) is None


@pytest.mark.parametrize("body", [
    "Acceptance:\n- the installed build runs on the iPhone (physical check)\n- verify the logs or document the gap with "
    "evidence",
    "Acceptance: verify the logs or document the gap with evidence. The physical qualification on the iPhone must "
    "also pass.",
])
def test_a_doc_branch_on_one_of_several_acceptance_items_is_not_met(body):
    """Review 6094541114 P2: a documentation alternative for one item must not direct whole-card completion."""
    assert ac.acceptance_alternative(body) is None
    assert ac.acceptance_met(BILLING["human_request"], body) is None


def test_an_unrelated_url_does_not_stand_in_for_a_cited_prs_link():
    """Review 6094541114 P2: a status URL suppressed the missing link for bare PR #13."""
    hr = copy.deepcopy(UNFORGED["human_request"])
    hr["scope"] += "\nStatus page: https://www.githubstatus.com/"
    assert any(m.startswith("where: PR/issue #13") for m in ac.missing(hr))
    hr["scope"] += "\nPR: https://github.com/softmax-universe/unforged/pull/13"
    assert not any(m.startswith("where: PR/issue") for m in ac.missing(hr))


@pytest.mark.parametrize("action", [
    "Unblock = A; archive = B",
    "Choose whether to expand the tests/fixtures-only limit to production code. Unblock = A; archive = B",
])
def test_bare_button_and_choose_whether_asks_are_refused(action):
    gaps = ac.missing({"action": action, "why_human": "Needs David.", "scope": "PR 13 only", "evidence": "x",
                       "interaction": "approval"})
    assert any(g.startswith("context:") for g in gaps)
    assert any(g.startswith("where:") for g in gaps)


def test_each_missing_part_is_named():
    gaps = ac.missing({"action": "Send it.", "why_human": "Tried: everything.", "scope": "Only this.",
                       "evidence": "x", "interaction": "reply"})
    assert [g.split(":")[0] for g in gaps] == ["context", "where", "buttons"]


def test_a_thin_option_is_named():
    hr = copy.deepcopy(IPHONE["human_request"])
    hr["scope"] = hr["scope"].replace("B) Archive: stop qualification; native remains the code default; no cutover.",
                                      "B) Archive: stop.")
    hr["choices"] = [{"label": "A (recommended)", "value": "A"}, {"label": "B", "value": "B"}]
    assert any(g.startswith("choices: option(s) Archive") for g in ac.missing(hr))


def test_the_gate_holds_a_thin_ask_then_releases_after_max_holds(gate, monkeypatch):
    monkeypatch.setattr(gate, "_card_body", lambda tool, args: UNFORGED["body"])
    request = dict(UNFORGED["human_request"])
    seen = []
    for _ in range(ac.MAX_HOLDS + 1):
        seen.append(gate._ask_context_hold("s1", "kanban_block", {"task_id": "t_x"}, request, card="t_x"))
    assert all(r and r["action"] == "block" and "ask_context_hold" in r["message"] for r in seen[:ac.MAX_HOLDS])
    assert seen[-1] is None
    monkeypatch.setattr(gate, "_card_body", lambda tool, args: BILLING["body"])
    r = gate._ask_context_hold("s2", "kanban_block", {"task_id": "t_b"}, BILLING_TICKET, card="t_b")
    assert r and "acceptance met without this; complete instead" in r["message"]
    monkeypatch.setattr(gate, "_card_body", lambda tool, args: IPHONE["body"])
    assert gate._ask_context_hold("s3", "kanban_block", {"task_id": "t_i"}, IPHONE["human_request"],
                                  card="t_i") is None
