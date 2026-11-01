"""Public scope fixtures: references and labels are not action identity."""
import json

import pytest

CARD = {"t_aabbccdd": {"id": "t_aabbccdd", "status": "done", "title": "Retire old integration", "result": "Old integration retired"}}
PHONE = "Approve D-04: remove audio_bridge phone kind from node_pairings?"
BOUNDARY = "D-4: preserve the autonomous agent privilege boundary; do not grant unattended administrative access."


def run(gate, ask, llm, *, structured=False, cards=None, ledger=None, users=None):
    jm = gate.judge_mod
    return jm.judge([ask], tools_text="", user_msgs=users or [], card_status=cards or {},
                    ledger_text=json.dumps(ledger or []), ledger=ledger or [], caps="",
                    llm=jm.structured_llm(llm, "") if structured else llm)[0]


@pytest.mark.parametrize("serialized", [False, True])
def test_historical_done_reference_reaches_semantic_judge(gate, serialized):
    ask = "Approve new integration access? Historical evidence: t_aabbccdd retired the old integration."
    if serialized:
        ask = json.dumps({"action": "Approve new integration access?", "evidence": ask})
    seen = []
    def llm(system, user):
        seen.append(user)
        return json.dumps({"asks": [{"i": 0, "verdict": "ok"}]})
    assert run(gate, ask, llm, structured=serialized, cards=CARD)["verdict"] == "ok"
    assert seen and "Retire old integration" in seen[0]


@pytest.mark.parametrize("structured", [False, True])
def test_different_scope_repeat_is_semantically_rechecked(gate, structured):
    calls = []
    def llm(system, user):
        calls.append(system)
        if len(calls) == 1:
            return json.dumps({"asks": [{"i": 0, "verdict": "already_answered", "reason": "D-4 was decided"}]})
        assert "subject" in system and "action" in system
        return json.dumps({"asks": [{"i": 0, "verdict": "ok", "reason": "Different subjects and actions; safe approval request"}]})
    assert run(gate, PHONE, llm, structured=structured, users=[BOUNDARY])["verdict"] == "ok"
    assert len(calls) == 2


def test_two_topic_words_are_not_deterministic_repeat(gate):
    ledger = [{"topic": "autonomous agent privilege boundary", "answer": "Keep administrative access manual"}]
    ask = "Approve autonomous agent logging retention?"
    seen = []
    def llm(system, user):
        seen.append(user)
        return '{"asks":[{"i":0,"verdict":"ok"}]}'
    assert run(gate, ask, llm, ledger=ledger)["verdict"] == "ok"
    assert seen


@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize("ask", [
    "Approve D-04: grant unattended administrative access to autonomous agents?",
    "May autonomous agents administer the system without a person approving each action?",
])
def test_changed_label_and_paraphrase_same_action_still_refused(gate, structured, ask):
    from conftest import llm_returning
    stub = llm_returning([("already_answered", "Same privilege escalation was declined", "", {
        "source": "messages", "source_quote": BOUNDARY, "request_quote": ask,
        "explanation": "Both concern granting unattended administrative authority to autonomous agents."})])
    if structured:
        with pytest.raises(ValueError, match="already_answered"):
            run(gate, ask, stub, structured=True, users=[BOUNDARY])
    else:
        assert run(gate, ask, stub, users=[BOUNDARY])["verdict"] == "already_answered"
    assert len(stub.seen) == 2


@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize("defect", ["missing", "fabricated", "wrong_source", "wrong_request"])
def test_ungrounded_repeat_never_counts_as_equivalence(gate, structured, defect):
    item = {"i": 0, "verdict": "already_answered", "equivalence": {
        "source": "messages", "source_quote": BOUNDARY, "request_quote": PHONE,
        "explanation": "Claimed same action"}}
    if defect == "missing":
        item.pop("equivalence")
    elif defect == "fabricated":
        item["equivalence"]["source_quote"] = "A decision not present in any source"
    elif defect == "wrong_source":
        item["equivalence"]["source"] = "draft"
    else:
        item["equivalence"]["request_quote"] = "A different requested action"
    stub = lambda *_: json.dumps({"asks": [item]})
    if structured:
        with pytest.raises(ValueError, match="unverified.*equivalence"):
            run(gate, PHONE, stub, structured=True, users=[BOUNDARY])
    else:
        assert run(gate, PHONE, stub, users=[BOUNDARY])["verdict"] == "ok"


@pytest.mark.parametrize("structured", [False, True])
def test_historical_done_does_not_ground_model_completion(gate, structured):
    calls = []
    def llm(system, user):
        calls.append(system)
        return json.dumps({"asks": [{"i": 0, "verdict": "already_done", "reason": "A historical card is done"}]})
    if structured:
        with pytest.raises(ValueError, match="unverified.*equivalence"):
            run(gate, PHONE, llm, structured=True, cards=CARD)
    else:
        assert run(gate, PHONE, llm, cards=CARD)["verdict"] == "ok"
    assert len(calls) == 2


@pytest.mark.parametrize("structured", [False, True])
def test_matching_completed_action_is_held(gate, structured):
    from conftest import llm_returning
    ask = "Could you retire the old integration?"
    stub = llm_returning([("already_done", "Old integration retired", "", {
        "source": "cards", "source_quote": "Old integration retired", "request_quote": ask,
        "explanation": "The completion result records retirement of the same old integration."})])
    if structured:
        with pytest.raises(ValueError, match="already_done"):
            run(gate, ask, stub, structured=True, cards=CARD)
    else:
        assert run(gate, ask, stub, cards=CARD)["verdict"] == "already_done"


@pytest.mark.parametrize("structured", [False, True])
def test_scope_recheck_preserves_safe_correction_for_secret_request(gate, structured):
    calls = []
    def llm(system, user):
        calls.append(system)
        if len(calls) == 1:
            return '{"asks":[{"i":0,"verdict":"already_answered"}]}'
        assert "secret" in system and "safe" in system
        return json.dumps({"asks": [{"i": 0, "verdict": "unverified", "reason": "No passwords in chat",
                                    "how": "Use the secure vault flow instead"}]})
    ask = "Different scope: send the password in chat for D-04."
    if structured:
        with pytest.raises(ValueError, match="unverified.*No passwords"):
            run(gate, ask, llm, structured=True, users=[BOUNDARY])
    else:
        assert run(gate, ask, llm, users=[BOUNDARY])["verdict"] == "unverified"


def test_repeat_recheck_does_not_release_other_denied_item(gate):
    calls = []
    def llm(system, user):
        calls.append(system)
        return json.dumps({"asks": [
            {"i": 0, "verdict": "already_answered" if len(calls) == 1 else "ok"},
            {"i": 1, "verdict": "unverified" if len(calls) == 1 else "ok", "how": "Use secure vault flow"}]})
    result = gate.judge_mod.judge([PHONE, "Send a password in chat"], tools_text="", user_msgs=[BOUNDARY],
                                 card_status={}, ledger_text="", caps="", llm=llm)
    assert [v["verdict"] for v in result] == ["ok", "unverified"]


@pytest.mark.parametrize("bad", ['not JSON', '{"asks": []}', '{"asks": [{"i": 9, "verdict": "already_answered"}]}'])
def test_malformed_initial_structured_verdict_is_not_repaired(gate, bad):
    calls = []
    def llm(*_):
        calls.append(1)
        return bad if len(calls) == 1 else '{"asks":[{"i":0,"verdict":"ok"}]}'
    with pytest.raises(ValueError):
        run(gate, PHONE, llm, structured=True)
    assert len(calls) == 1


@pytest.mark.parametrize("response", ['not JSON', '{"asks": []}'])
def test_malformed_recheck_preserves_other_denials(gate, response):
    calls = []
    def llm(*_):
        calls.append(1)
        return json.dumps({"asks": [{"i": 0, "verdict": "already_answered"},
                                   {"i": 1, "verdict": "unverified", "how": "Use secure vault flow"}]}) if len(calls) == 1 else response
    result = gate.judge_mod.judge([PHONE, "Send a password in chat"], tools_text="", user_msgs=[],
                                 card_status={}, ledger_text="", caps="", llm=llm)
    assert [v["verdict"] for v in result] == ["ok", "unverified"]


@pytest.mark.parametrize("structured", [False, True])
def test_recheck_provider_failure_retains_exception_policy(gate, structured):
    calls = []
    def llm(*_):
        calls.append(1)
        if len(calls) == 1:
            return '{"asks":[{"i":0,"verdict":"already_answered"}]}'
        raise RuntimeError("provider offline")
    if structured:
        with pytest.raises(ValueError, match="unverified.*could not be rechecked"):
            run(gate, PHONE, llm, structured=True)
    else:
        assert run(gate, PHONE, llm)["verdict"] == "ok"
    assert len(calls) == 2


@pytest.mark.parametrize("raw", [
    'Here is my verdict: {"asks":[{"i":0,"verdict":"already_done"}]}',
    '{"asks":[{"i":0,"verdict":" Already_Done "}]}',
])
def test_wrapped_ordinary_json_cannot_bypass_repeat_validation(gate, raw):
    calls = []
    def llm(*_):
        calls.append(1)
        return raw
    assert run(gate, PHONE, llm, cards=CARD)["verdict"] == "ok"
    assert len(calls) == 2


def test_exact_target_stale_wait_is_still_deterministic(gate):
    def forbidden(*_):
        raise AssertionError("exact card target needs no model")
    assert run(gate, "Still waiting on you: **t_aabbccdd**.", forbidden, cards=CARD)["verdict"] == "already_done"


@pytest.mark.parametrize("tool", ["kanban_create", "kanban_block"])
@pytest.mark.parametrize("initial", ["ok", "already_answered", "already_done"])
def test_structured_hook_historical_evidence_and_scope_recheck(gate, board, tool, initial):
    board("t_aabbccdd", "done", title="Retire old integration", result="Old integration retired")
    gate.LOG.add("s1", "terminal", "integration access report", "New integration needs owner approval")
    gate.LOG.set_users("s1", [BOUNDARY])
    calls = []
    def llm(system, user):
        calls.append((system, user))
        return json.dumps({"asks": [{"i": 0, "verdict": initial if len(calls) == 1 else "ok"}]})
    gate.LLM = llm
    result = gate.pre_tool_call(tool_name=tool, session_id="s1", args={
        "initial_status": "blocked", "reason": "Owner approval", "human_request": {
            "action": PHONE, "scope": "Only phone-kind registration; no autonomous agent permissions",
            "why_human": "Owner chooses whether to remove this device kind", "interaction": "approval",
            "evidence": "terminal integration access report: New integration needs owner approval. Historical context: t_aabbccdd"}})
    assert result is None
    assert len(calls) == (1 if initial == "ok" else 2)
    assert "t_aabbccdd: done" in calls[0][1]
