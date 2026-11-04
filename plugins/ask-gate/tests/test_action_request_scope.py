"""action_request judging sees the WHOLE structured ActionRequest, independent of paragraph order.

Witnessed failure (synthetic equivalent here): a long approval plan for a NEW study reused the label "Stage B"
from an earlier, finished study. The hook judged only ``title + scope.markdown[:600]``, so the object, the new
limits, exclusions and source refs further down never reached the judge, and a short earlier "run stage b"
for the other study held the new request as already_answered. The refusal then quoted whichever of David's
messages shared the most words with the ask, not the evidence the verdict cited.

Fixtures are synthetic two-study plans; the model is an injected stub, HERMES_HOME and the ledger live under
tmp_path, and nothing is published or resolved.
"""
import json

import pytest

from conftest import llm_returning

RID = "0b9f3c1e-6a3d-4c55-9a1e-7d2a51f0c001"
OLD_APPROVAL = "run stage b, you may rent compute"
# The message that shares the most words with the new plan, but is NOT what any verdict cites.
DECOY = ("stage a for the sequence digits study is approved: implementation and correctness tests only, "
         "seven cells, frozen seeds, worker CPU, sequence digits directory")
SETTLED = ("## What is already settled\n"
           + "Stage A was approved earlier, independently verified and merged; its tests replayed green. " * 8
           + "\nThis is not another Stage A approval request.\n")
OBJECT = ("## Object\nRun exactly seven frozen confirmation cells of the SEQUENCE-DIGITS study under "
          "`studies/sequence-digits/` (not the finished lattice-rendering study under `studies/lattice-render/`).")
LIMIT_KEY, LIMIT_VALUE = "CPU budget", "53 owned CPU core-hours, 48-hour wall clock, $0"
EXCLUSION = "No rentals and no GPU; never touch studies/lattice-render/"
REF_ID = "t_00000000000000ab"
OUTCOME = "Seven confirmation runs start on owned worker CPU with the frozen protocol"


def _request(markdown, *, limits=None, exclusions=None, refs=None, title="Approve Stage B: seven frozen runs"):
    return {"requestId": RID, "expectedRevision": 1, "request": {
        "schemaVersion": 2, "kind": "approval", "title": title,
        "scope": {"markdown": markdown, "complete": True, "sourceRefs": [
            {"gatewayId": "hub", "profile": "default", "type": "kanban", "id": r, "board": "default"}
            for r in (refs if refs is not None else [REF_ID])]},
        "limits": limits if limits is not None else [{"key": LIMIT_KEY, "value": LIMIT_VALUE,
                                                      "permissionRequired": True}],
        "exclusions": exclusions if exclusions is not None else [EXCLUSION],
        "outcomes": {"approve": OUTCOME, "revise": "Stage B stays unstarted", "decline": "Nothing runs"},
        "choices": [{"id": "approve", "label": "Approve Stage B", "effect": "approve"},
                    {"id": "revise", "label": "Change", "effect": "revise"},
                    {"id": "decline", "label": "Decline", "effect": "decline"}],
        "aliases": [], "state": "open"}}


def _plan(order):
    head = "# Decision: run Stage B\n\n"
    return head + (SETTLED + "\n" + OBJECT if order == "late" else OBJECT + "\n\n" + SETTLED)


@pytest.fixture
def ar(gate, tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    monkeypatch.setattr(gate, "FALLBACKS", lambda: [])
    gate.LOG.set_users("s1", [OLD_APPROVAL, DECOY, "thanks"])
    assert all(str(p).startswith(str(tmp_path)) for p in gate.core.boards())
    yield gate
    assert not (tmp_path / "kanban.db").exists()


def _asks(prompt):
    return prompt.split("ASK SENTENCES:\n", 1)[1].split("\n\nFULL DRAFT REPLY", 1)[0]


def _ok_stub():
    return llm_returning([("ok", "a new study with its own limits")])


# ---- 1. the judge receives the full structured identity --------------------------------------------------

@pytest.mark.parametrize("order", ["late", "early"])
def test_judge_sees_full_scope_limits_exclusions_refs_in_any_order(ar, order):
    plan = _plan(order)
    assert order == "early" or plan.index("SEQUENCE-DIGITS") > 600  # the distinction sits past the old cut
    stub = _ok_stub()
    ar.LLM = stub
    assert ar.pre_tool_call(tool_name="action_request", session_id="s1", args=_request(plan)) is None
    asks = _asks(stub.seen[0][1])
    for needle in ("SEQUENCE-DIGITS", "studies/sequence-digits/", "not another Stage A approval request",
                   LIMIT_KEY, LIMIT_VALUE, "permission required", EXCLUSION, REF_ID, OUTCOME, RID,
                   "expectedRevision 1", "Approve Stage B: seven frozen runs"):
        assert needle in asks, needle


def test_late_limits_and_refs_change_the_identity_the_judge_reads(ar):
    """Two requests with the same title and markdown but different late limits/refs are judged as different."""
    seen = []
    for limit, ref in (("53 owned CPU core-hours", REF_ID), ("400 rented GPU-hours", "t_00000000000000cd")):
        stub = _ok_stub()
        ar.LLM = stub
        ar.pre_tool_call(tool_name="action_request", session_id="s1",
                         args=_request(_plan("late"), limits=[{"key": LIMIT_KEY, "value": limit}], refs=[ref]))
        seen.append(_asks(stub.seen[0][1]))
    assert "53 owned CPU core-hours" in seen[0] and "400 rented GPU-hours" not in seen[0]
    assert "400 rented GPU-hours" in seen[1] and "t_00000000000000cd" in seen[1]


# ---- 2. genuine repeats still hold; label-only and changed scope do not inherit ---------------------------

def _repeat(source_quote, request_quote):
    return llm_returning([("already_answered", "David already approved exactly this", "", {
        "source": "messages", "source_quote": source_quote, "request_quote": request_quote,
        "explanation": "both name the same seven sequence-digits confirmation runs"})])


def test_same_study_repeat_grounded_in_late_scope_still_holds(ar):
    ar.LOG.set_users("s1", [OLD_APPROVAL, DECOY,
                            "run stage b of the sequence-digits study on owned cpu, 53 core-hours"])
    stub = _repeat("run stage b of the sequence-digits study on owned cpu, 53 core-hours",
                   "seven frozen confirmation cells of the SEQUENCE-DIGITS study")
    ar.LLM = stub
    r = ar.pre_tool_call(tool_name="action_request", session_id="s1", args=_request(_plan("late")))
    assert r and r["action"] == "block" and "already_answered" in r["message"], r
    assert len(stub.seen) == 2  # initial verdict + independent equivalence recheck


def test_reused_stage_label_alone_is_not_equivalence(ar):
    """The old study's short "run stage b" cannot hold a new study's request through the shared label."""
    for request_quote in ("Approve Stage B", "run Stage B", "Stage B"):
        ar._AR_HELD.clear()
        stub = _repeat(OLD_APPROVAL, request_quote)
        ar.LLM = stub
        assert ar.pre_tool_call(tool_name="action_request", session_id="s1",
                                args=_request(_plan("late"))) is None, request_quote


def test_changed_scope_rechecked_as_new_decision_passes(ar):
    replies = iter([json.dumps({"asks": [{"i": 0, "verdict": "already_answered", "reason": "stage b approved"}]}),
                    json.dumps({"asks": [{"i": 0, "verdict": "ok", "reason": "different study and limits"}]})])
    seen = []

    def llm(system, user):
        seen.append(user)
        return next(replies)
    ar.LLM = llm
    assert ar.pre_tool_call(tool_name="action_request", session_id="s1", args=_request(_plan("late"))) is None
    assert len(seen) == 2 and all(LIMIT_VALUE in _asks(u) for u in seen)


def test_equivalence_rule_names_reused_labels(ar):
    assert "Stage B" in ar.judge_mod.EQUIVALENCE_SYSTEM


# ---- 3. diagnostics quote the verdict's own evidence -----------------------------------------------------

def test_hold_quotes_the_verdicts_source_not_token_overlap(ar):
    ar.LOG.set_users("s1", [OLD_APPROVAL, DECOY, "go ahead with stage b of the sequence-digits study"])
    ar.LLM = _repeat("go ahead with stage b of the sequence-digits study",
                     "seven frozen confirmation cells of the SEQUENCE-DIGITS study")
    r = ar.pre_tool_call(tool_name="action_request", session_id="s1", args=_request(_plan("late")))
    assert r and r["action"] == "block"
    assert "go ahead with stage b of the sequence-digits study" in r["message"]
    assert "seven frozen confirmation cells of the SEQUENCE-DIGITS study" in r["message"]
    assert DECOY[:60] not in r["message"]  # the highest token-overlap message is not evidence


def test_hold_without_source_quote_says_evidence_unavailable(ar):
    ar.LLM = llm_returning([("agent_can_do", "token-broker cf-infra edits DNS", "use the cf-infra scope")])
    r = ar.pre_tool_call(tool_name="action_request", session_id="s1", args=_request(_plan("late")))
    assert r and r["action"] == "block" and "agent_can_do" in r["message"]
    assert "unavailable" in r["message"].lower()
    assert DECOY[:60] not in r["message"] and OLD_APPROVAL not in r["message"]


# ---- 4. explicit large-input policy ----------------------------------------------------------------------

def test_oversize_request_is_marked_and_cannot_be_held_as_repeat(ar):
    cap = ar.AR_IDENTITY_CAP
    plan = _plan("late") + "\n" + ("filler paragraph about protocol details. " * (cap // 40 + 50)) + "\nEND-MARKER"
    ar.LLM = _repeat("go ahead with stage b of the sequence-digits study",
                     "seven frozen confirmation cells of the SEQUENCE-DIGITS study")
    ar.LOG.set_users("s1", ["go ahead with stage b of the sequence-digits study"])
    stub = ar.LLM
    assert ar.pre_tool_call(tool_name="action_request", session_id="s1", args=_request(plan)) is None
    asks = _asks(stub.seen[0][1])
    assert len(asks) <= cap + 400
    assert "omitted" in asks and "END-MARKER" in asks  # head and tail kept, the cut is explicit
    for needle in (LIMIT_VALUE, EXCLUSION, REF_ID, OUTCOME):  # structured fields are never cut
        assert needle in asks
    log = [json.loads(x) for x in (ar.core.home() / "logs" / "ask-gate.jsonl").read_text().splitlines()]
    assert any(e.get("kind") == "action_request_repeat_unproven" for e in log)


def test_oversize_request_agent_can_do_still_holds(ar):
    cap = ar.AR_IDENTITY_CAP
    plan = _plan("late") + "\n" + ("filler. " * (cap // 8 + 50))
    ar.LLM = llm_returning([("agent_can_do", "token-broker cf-infra edits DNS", "use the cf-infra scope")])
    r = ar.pre_tool_call(tool_name="action_request", session_id="s1", args=_request(plan))
    assert r and r["action"] == "block" and "agent_can_do" in r["message"]


# ---- 5. real direct and deferred dispatch through core ---------------------------------------------------

@pytest.mark.parametrize("deferred", [False, True])
@pytest.mark.parametrize("case", ["new_study", "same_study"])
def test_core_dispatch_action_request_scope(ar, monkeypatch, deferred, case):
    """Real bridge recursion and pre-dispatch hook input; no registry handler, nothing published."""
    monkeypatch.setenv("HERMES_DISABLE_LAZY_INSTALLS", "1")
    import model_tools
    import hermes_cli.plugins as plugins
    from tools import tool_search
    from types import SimpleNamespace

    if case == "same_study":
        ar.LOG.set_users("s1", [OLD_APPROVAL, "run stage b of the sequence-digits study on owned cpu"])
        stub = _repeat("run stage b of the sequence-digits study on owned cpu",
                       "seven frozen confirmation cells of the SEQUENCE-DIGITS study")
    else:
        stub = _repeat(OLD_APPROVAL, "Approve Stage B")
    ar.LLM = stub
    names = {"action_request"}
    monkeypatch.setattr(model_tools, "get_tool_definitions", lambda **_: [])
    monkeypatch.setattr(tool_search, "load_config_readonly", lambda: SimpleNamespace(
        effective_defer_tools=names, eager_tools=set(), forward_direct_calls=False))
    monkeypatch.setattr(tool_search, "session_unavailable_error", lambda *_: None)
    monkeypatch.setattr(tool_search, "scoped_deferrable_names", lambda *_: names)
    monkeypatch.setattr(tool_search, "validate_deferred_call_args", lambda *_: None)
    seen, executed = [], []

    def hooks(name, payload, **kw):
        seen.append((name, payload))
        result = ar.pre_tool_call(tool_name=name, args=payload, session_id=kw["session_id"])
        return (result["message"], None) if result else (None, None)

    monkeypatch.setattr(plugins, "_dispatch_pre_tool_call_hooks", hooks)
    monkeypatch.setattr(model_tools, "_emit_post_tool_call_hook", lambda **_: None)
    monkeypatch.setattr(model_tools, "_apply_transform_tool_result_hook", lambda n, a, r, *rest: r)
    monkeypatch.setattr(model_tools, "_execute_tool", lambda name, *a, **kw:
                        executed.append(name) or '{"fixture_only":true}')
    args = _request(_plan("late"))
    payload = {"calls": [{"name": "action_request", "arguments": args}]} if deferred else args
    result = model_tools.handle_function_call("tool_call" if deferred else "action_request", payload,
                                              session_id="s1", skip_tool_request_middleware=True)
    assert [n for n, _ in seen] == ["action_request"]
    assert seen[0][1]["request"]["scope"]["markdown"] == args["request"]["scope"]["markdown"]
    asks = _asks(stub.seen[0][1])
    assert "studies/sequence-digits/" in asks and LIMIT_VALUE in asks and REF_ID in asks
    if case == "same_study":
        assert "already_answered" in result and executed == []
    else:
        assert json.loads(result) == {"fixture_only": True} and executed == ["action_request"]


# ---- 6. review findings: long text must not change deterministic screens or regress short asks ------------

def test_irreversible_screen_is_linear_on_long_text(gate):
    import time
    text = "Approve the plan. " + "ordinary protocol detail about frozen seeds and windows. " * 800  # ~45k chars
    t0 = time.perf_counter()
    assert gate.judge_mod.IRREVERSIBLE.search(text) is None
    assert gate.judge_mod.IRREVERSIBLE.search(text + " then delete the old backup") is not None
    assert time.perf_counter() - t0 < 0.5


def test_descriptive_cant_sentence_in_plan_still_reaches_judge(ar):
    plan = _plan("late") + "\nA GPU variant would need hardware the study doesn't have, so it is excluded."
    stub = _repeat("run stage b of the sequence-digits study on owned cpu",
                   "seven frozen confirmation cells of the SEQUENCE-DIGITS study")
    ar.LOG.set_users("s1", ["run stage b of the sequence-digits study on owned cpu"])
    ar.LLM = stub
    r = ar.pre_tool_call(tool_name="action_request", session_id="s1", args=_request(plan))
    assert stub.seen, "a descriptive sentence deep in the plan must not short-circuit the judge"
    assert r and "already_answered" in r["message"]


def test_action_request_is_never_routed_to_the_soft_instruction_prompt(ar):
    plan = "Please check the frozen protocol before you approve.\n" + _plan("late")
    stub = _ok_stub()
    ar.LLM = stub
    ar.pre_tool_call(tool_name="action_request", session_id="s1", args=_request(plan))
    assert stub.seen and stub.seen[0][0] == ar.judge_mod.SYSTEM


def test_short_ask_that_is_only_a_label_can_still_be_a_repeat(gate):
    """A draft "Run stage B?" right after David said "run stage b" quotes the only words it has."""
    llm = llm_returning([("already_answered", "he said run stage b", "", {
        "source": "messages", "source_quote": "run stage b", "request_quote": "Run stage B",
        "explanation": "same label, same plan, nothing else in this ask"})])
    v = gate.judge_mod.judge(["Run stage B?"], tools_text="", user_msgs=["run stage b"], card_status={},
                             ledger_text="", caps="", llm=llm)[0]
    assert v["verdict"] == "already_answered"


@pytest.mark.parametrize("request_quote", ["Stage B approval", "Yes, run stage B", "approve the Stage-B run",
                                           "Run Stage B now", "start stage two", "Approve Stage B [approve]"])
def test_label_variants_alone_are_not_equivalence(ar, request_quote):
    ar.LLM = _repeat(OLD_APPROVAL, request_quote)
    plan = _plan("late") + f"\nQuoted label forms: {request_quote}."
    assert ar.pre_tool_call(tool_name="action_request", session_id="s1", args=_request(plan)) is None


def test_label_only_source_must_share_the_requests_object(ar):
    """Another study's terse "run stage b" cannot ground object-bearing words of a new study's request."""
    ar.LLM = _repeat(OLD_APPROVAL, "seven frozen confirmation cells of the SEQUENCE-DIGITS study")
    assert ar.pre_tool_call(tool_name="action_request", session_id="s1", args=_request(_plan("late"))) is None


def test_label_source_naming_the_same_object_still_holds(ar):
    ar.LOG.set_users("s1", ["run stage b for sequence-digits"])
    ar.LLM = _repeat("run stage b for sequence-digits",
                     "seven frozen confirmation cells of the SEQUENCE-DIGITS study")
    r = ar.pre_tool_call(tool_name="action_request", session_id="s1", args=_request(_plan("late")))
    assert r and "already_answered" in r["message"]


def test_any_clipped_field_counts_as_incomplete_identity(ar):
    """Policy: a clipped title or structured item is as incomplete as a cut plan, so no repeat hold."""
    ar.LOG.set_users("s1", ["run stage b of the sequence-digits study on owned cpu"])
    ar.LLM = _repeat("run stage b of the sequence-digits study on owned cpu",
                     "seven frozen confirmation cells of the SEQUENCE-DIGITS study")
    long_exclusion = "No rentals. " + "x" * (ar._AR_ITEM_CAP + 10)
    assert ar.pre_tool_call(tool_name="action_request", session_id="s1",
                            args=_request(_plan("late"), exclusions=[long_exclusion])) is None


def test_many_structured_items_stay_within_the_cap(ar):
    stub = _ok_stub()
    ar.LLM = stub
    limits = [{"key": f"limit {i}", "value": "y" * 1400} for i in range(60)]
    ar.pre_tool_call(tool_name="action_request", session_id="s1", args=_request(_plan("late"), limits=limits))
    assert len(_asks(stub.seen[0][1])) <= ar.AR_IDENTITY_CAP + 400


def test_identity_is_not_duplicated_as_the_draft_reply(ar):
    stub = _ok_stub()
    ar.LLM = stub
    ar.pre_tool_call(tool_name="action_request", session_id="s1", args=_request(_plan("late")))
    reply = stub.seen[0][1].split("FULL DRAFT REPLY (context):\n", 1)[1].split("\n\nTHIS TURN", 1)[0]
    assert "SEQUENCE-DIGITS" not in reply


# ---- 7. second review: the label backstop is scoped to action_request and never eats ordinary words ---------

def test_reply_path_repeat_with_object_after_label_approval_still_holds(gate):
    """Ordinary asks keep their semantic recheck: the label backstop is action_request-only."""
    ask = "Should I run stage B of the fly-brain sweep on the b300 node now?"
    llm = llm_returning([("already_answered", "he said run stage b", "", {
        "source": "messages", "source_quote": "run stage b", "request_quote": "run stage B of the fly-brain sweep",
        "explanation": "his go-ahead answered the stage B proposal for this sweep"})])
    v = gate.judge_mod.judge([ask], tools_text="", user_msgs=["fly-brain stage A looks good", "run stage b"],
                             card_status={}, ledger_text="", caps="", llm=llm)[0]
    assert v["verdict"] == "already_answered"


def test_short_action_request_that_is_only_a_label_can_still_be_a_repeat(ar):
    ar.LOG.set_users("s1", ["run stage b"])
    ar.LLM = _repeat("run stage b", "Run stage B")
    args = _request("", limits=[], exclusions=[], refs=[], title="Run stage B?")
    args["request"]["outcomes"] = {"revise": "x"}
    r = ar.pre_tool_call(tool_name="action_request", session_id="s1", args=args)
    assert r and "already_answered" in r["message"], r


@pytest.mark.parametrize("word", ["steps", "stages", "parts", "party", "rounds", "gates", "staged", "phased"])
def test_stage_label_does_not_match_ordinary_words(gate, word):
    jm = gate.judge_mod
    assert not jm._STAGE_LABEL.search(f"go ahead and do those {word} today")
    assert word in jm._content(f"the {word} list")


def test_clipped_request_id_counts_as_incomplete(ar):
    ar.LOG.set_users("s1", ["run stage b of the sequence-digits study on owned cpu"])
    ar.LLM = _repeat("run stage b of the sequence-digits study on owned cpu",
                     "seven frozen confirmation cells of the SEQUENCE-DIGITS study")
    args = _request(_plan("late"))
    args["requestId"] = "r" * 200
    assert ar.pre_tool_call(tool_name="action_request", session_id="s1", args=args) is None
