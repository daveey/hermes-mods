"""Public helpers for session-outcome (open_steps, add_step) and the assent-echo filter."""
import json

import pytest

from test_user_steps import add_session, core, fake_llm, home, ledger, run  # noqa: F401 (fixture)


def test_add_step_writes_choose_step_with_options(home):
    add_session(home, "S1")
    lg = ledger(home)
    step = core.add_step("S1", "Ship build 412 to prod?", [{"label": "Now", "value": "now"}, "Later"], ledger=lg)
    assert step["id"].startswith("us_") and step["status"] == "open" and step["session"] == "S1"
    assert step["kind"] == "choose" and step["options"] == ["Now", "Later"]
    assert step["choices"] == [{"label": "Now", "value": "now"}, {"label": "Later", "value": "Later"}]
    # the labels are literal in the source paragraph, so migration turns them into buttons
    assert core.literal_options(step) == ["Now", "Later"]
    stored = lg.read("S1")["steps"]
    assert [s["id"] for s in stored] == [step["id"]]


def test_add_step_dedupes_open_question_and_needs_text(home):
    add_session(home, "S1")
    lg = ledger(home)
    a = core.add_step("S1", "Which region?", ["us", "eu"], ledger=lg)
    b = core.add_step("S1", "which  region?", ["us", "eu"], ledger=lg)
    assert a["id"] == b["id"] and len(lg.read("S1")["steps"]) == 1
    assert core.add_step("S1", "Your name?", [], ledger=lg)["kind"] == "input"
    with pytest.raises(ValueError):
        core.add_step("S1", "  ", ["a", "b"], ledger=lg)


def test_open_steps_lists_open_only_oldest_first(home):
    add_session(home, "S1")
    lg = ledger(home)
    first = core.add_step("S1", "First?", ["a", "b"], ledger=lg, now=100.0)
    second = core.add_step("S1", "Second?", ["c", "d"], ledger=lg, now=200.0)
    core.resolve(lg, lg.root("S1"), [first["id"]], "a", "user")
    got = core.open_steps("S1", ledger=lg)
    assert [s["id"] for s in got] == [second["id"]]
    assert got[0]["text"] == "Second?" and got[0]["choices"] == [{"label": "c", "value": "c"},
                                                                  {"label": "d", "value": "d"}]
    assert core.open_steps("", ledger=lg) == []


def test_open_steps_follows_an_open_canonical_request_whose_ledger_step_was_retired(home):
    """hub 20261030_071719_cde14c: the extractor retired the ledger step while its canonical request (what the For
    you queue shows) stayed open; session-outcome read the ledger only and the chat fell to unresolved{no_summary}."""
    add_session(home, "S1")
    lg = ledger(home)
    step = core.add_step("S1", "Build the combined admin per build-plan.md?", ["Go", "Hold"], ledger=lg)
    req = core.ActionStore().migrate_step(core.server_owner("S1"), step)
    assert [s["id"] for s in core.open_steps("S1", ledger=lg)] == [step["id"]]  # mirrored once, not twice
    core.resolve(lg, lg.root("S1"), [step["id"]], "answered in chat", "model")
    got = core.open_steps("S1", ledger=lg)
    assert [s["id"] for s in got] == [req["requestId"]]
    assert got[0]["text"] == "Build the combined admin per build-plan.md?" and got[0]["status"] == "open"
    assert [c["label"] for c in got[0]["choices"]][:2] == ["Go", "Hold"]


def test_open_steps_drops_a_decided_canonical_request(home):
    add_session(home, "S1")
    lg = ledger(home)
    step = core.add_step("S1", "Build the combined admin per build-plan.md?", ["Go", "Hold"], ledger=lg)
    owner = core.server_owner("S1")
    req = core.ActionStore().migrate_step(owner, step)
    core.resolve(lg, lg.root("S1"), [step["id"]], "answered in chat", "model")
    core.ActionStore().withdraw(owner, req["requestId"], by="user", reason="not needed")
    assert core.open_steps("S1", ledger=lg) == []


@pytest.mark.parametrize("span", ["I'd say yes.", "Yes, please.", "Sure", "Sounds good!", "OK then", "Go ahead."])
def test_assent_echo_is_dropped(span):
    reply = f"The build is green.\n\n{span}"
    raw = [{"text": span, "kind": "input", "verbatim_span": span}]
    assert core.assent_echo(span)
    assert core.validate(raw, reply, []) == []


@pytest.mark.parametrize("span", ["Yes or no: should I deploy build 412 now?", "Should I deploy it?",
                                  "Which region do you want?"])
def test_real_asks_are_not_assent(span):
    reply = f"The build is green.\n\n{span}"
    assert not core.assent_echo(span)
    raw = [{"text": span, "kind": "input", "verbatim_span": span}]
    assert [s["verbatim_span"] for s in core.validate(raw, reply, [])] == [span]


def test_assent_echo_never_mints_a_tile(home):
    add_session(home, "S1")
    reply = "Thanks, you checked the logs.\n\nI'd say yes."
    llm = fake_llm({"steps": [{"text": "I'd say yes.", "kind": "approve", "verbatim_span": "I'd say yes."}],
                    "resolved": []})
    out = run(home, reply, llm=llm)
    assert llm.calls, "the extractor must run for this to test the filter"
    assert out["steps"] == [] and ledger(home).open_steps("S1") == []
