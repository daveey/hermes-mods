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
