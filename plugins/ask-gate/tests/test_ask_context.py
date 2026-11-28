"""ask_context_hold + acceptance_met (ask_context.py), on the three hub asks behind t_01a21f5e47abf118.

* t_01a21bb1169deea2 (unforged CI, PR #13): cites a PR with no URL, and its recommended option's cost is "additional
  agent work" with no size. Held.
* t_01a211fcd5927efb (iPhone qualification): device, steps, costs and both buttons. Passes.
* t_01a21ef258f83d8d (billing watchdog): its acceptance "verify the affected workflows or document the external
  hold with evidence" needs no ticket read. Held as acceptance_met.
"""
import copy
import importlib.util
import json
import sys
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
    hit = ac.hit(BILLING["human_request"], BILLING["body"])
    assert hit and hit["rule"] == "acceptance_met"
    assert hit["message"].startswith("ask-gate: acceptance met without this; complete instead.")
    assert "document the external hold with evidence" in hit["message"]


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


def test_a_negated_payment_mention_does_not_exempt_the_billing_ask():
    assert "No payment or limit increase is requested" in BILLING["human_request"]["action"]
    assert ac.acceptance_met(BILLING["human_request"], BILLING["body"]) is not None


@pytest.mark.parametrize("why_human, action", [
    ("Only you can authorize this action", "No permission change is needed, but approve a one-time $50 payment"),
    ("I am not authorized to pay $50 without your approval", "Approve it in the GitHub billing page"),
])
def test_negation_removal_keeps_an_affirmative_payment(why_human, action):
    """Review 6094667454 P2: negation removal swallowed a later affirmative $50 approval and a human-only payment."""
    hr = dict(BILLING["human_request"], why_human=why_human, scope="Only this operation",
              action=action + ". Unblock = do it; archive = drop it.")
    assert ac.acceptance_met(hr, BILLING["body"]) is None
    assert ac.hit(hr, BILLING["body"]) is None or ac.hit(hr, BILLING["body"])["rule"] != "acceptance_met"


@pytest.mark.parametrize("why_human, action", [
    ("Only you can read the invoice status through your signed-in browser",
     "Open the billing support ticket and supply its latest status"),
    ("Only you can see the subscription status page", "Read the subscription status and paste it here"),
    ("Only you can open the budget report", "Send me this month's budget report"),
])
def test_read_only_financial_context_does_not_exempt_the_ask(why_human, action):
    """Review 6094667454 P2: invoice/subscription/budget as subject matter is not a money change."""
    hr = dict(BILLING["human_request"], why_human=why_human, scope="Only this operation",
              action=action + ". Unblock = do it; archive = drop it.")
    hit = ac.hit(hr, BILLING["body"])
    assert hit and hit["rule"] == "acceptance_met"


@pytest.mark.parametrize("text", [
    "Pay the March invoice", "Raise the monthly budget to cover the overage", "Cancel the Superhuman subscription",
    # Review 6094768133 P2: money changes without a $ amount or a listed verb before the noun.
    "Confirm the refund for order 4411", "Process the refund for the duplicate order",
    "Refund the customer for the duplicate order", "Settle the outstanding invoice",
    "Submit the invoice to the vendor portal", "Confirm the subscription renewal",
    "Okay the budget increase for this sprint", "Sign off on the budget increase",
    "Accept the new subscription terms",
])
def test_a_financial_change_is_still_exempt(text):
    hr = dict(BILLING["human_request"], action=text + ". Unblock = do it; archive = drop it.")
    assert ac.acceptance_met(hr, BILLING["body"]) is None


@pytest.mark.parametrize("text", [
    # Review 6094768133 P3: read-only subject matter and negated payments are not money changes.
    "Start by reading the invoice status", "Add the invoice link to this card",
    "Look into the issue with the invoice", "Nothing to pay here", "You won't need to pay anything",
])
def test_read_only_or_negated_money_text_is_not_a_change(text):
    hr = dict(BILLING["human_request"], why_human="Only you can do this", scope="Only this operation",
              action=text + ". Unblock = do it; archive = drop it.")
    assert ac.acceptance_met(hr, BILLING["body"]) is not None


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
    r = gate._ask_context_hold("s2", "kanban_block", {"task_id": "t_b"}, BILLING["human_request"], card="t_b")
    assert r and "acceptance met without this; complete instead" in r["message"]
    monkeypatch.setattr(gate, "_card_body", lambda tool, args: IPHONE["body"])
    assert gate._ask_context_hold("s3", "kanban_block", {"task_id": "t_i"}, IPHONE["human_request"],
                                  card="t_i") is None
