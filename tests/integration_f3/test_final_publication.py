"""Real discovery, hooks, turn loop, source reconciliation and publication boundary."""
import json
from types import SimpleNamespace

import pytest


ASK = "Please choose the blue or green case for the device."
OTHER = "Please choose the red or yellow case for the device."


def extraction(text=ASK):
    return {"kind": "choose", "text": text, "verbatim_span": text,
            "options": ["blue", "green"] if text == ASK else ["red", "yellow"]}


def requests(h):
    return h.steps.core.ActionStore().requests(h.steps.core.server_owner(h.agent.session_id))


def retire(h, step):
    """Resolve through canonical decision + confirmed delivery, never the legacy ledger API."""
    core = h.steps.core
    store = core.ActionStore()
    owner = core.server_owner(h.agent.session_id)
    request = store.get(owner, step['requestId'])
    choice = next(c for c in request['choices'] if c['effect'] == 'answer')
    result = store.decide(owner, dict(requestId=request['requestId'],
        expectedRevision=request['revision'], choiceId=choice['id'], revisionText=choice['label'],
        idempotencyKey='f3-retire-' + step['id']))
    receiver = SimpleNamespace(deliver=lambda op: {'deliveryId': op['operationId']},
                               readback=lambda op, receipt: receipt['deliveryId'] == op['operationId'])
    delivered = store.dispatch(owner, result['operation']['operationId'], receiver)
    assert delivered['request']['state'] == 'resolved'
    assert not core.open_steps(h.agent.session_id)


class Cards:
    """Trap obsolete rolling-card delivery: canonical requests must not call it."""
    def __init__(self):
        self.calls = []

    def create(self, session_id, title, body, key):
        self.calls.append(("create", session_id, title, body, key))
        return {"id": "test-card", "board": "test"}

    def update(self, card, title, body):
        self.calls.append(("update", card, title, body))

    def archive(self, card):
        self.calls.append(("archive", card))


class Model:
    def __init__(self, agent, monkeypatch, answers):
        self.screen, self.interims, self.requests = [], [], []
        self.answers = iter(answers)
        self.agent = agent
        agent.stream_delta_callback = self.screen.append
        agent.interim_assistant_callback = lambda text, **kw: self.interims.append(text)
        monkeypatch.setattr(agent, "_interruptible_api_call", self.call)
        monkeypatch.setattr(agent, "_interruptible_streaming_api_call", self.call)

    def call(self, request, **kw):
        self.requests.append(request)
        text = next(self.answers)
        self.agent._fire_stream_delta(text)
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=text, tool_calls=None), finish_reason="stop")],
            model="test/model", usage=None)

    @property
    def shown(self):
        return "".join(x for x in self.screen if isinstance(x, str)) + "".join(self.interims)


def wire_models(h, monkeypatch, events, candidate=ASK, reject_first=False):
    cards = Cards()
    monkeypatch.setattr(h.steps.cards_mod, "HermesCards", lambda: cards)

    def judge(session_id, asks, tools, reply="", **kwargs):
        events.append(("judge", reply))
        verdict = "agent_can_do" if reject_first and len(events) == 1 else "ok"
        return [{"i": i, "verdict": verdict, "reason": "Test judgment."} for i in range(len(asks))]

    def extract(system, prompt):
        reply = prompt.split("ASSISTANT REPLY:\n", 1)[1]
        events.append(("extract", reply))
        text = ASK if ASK in reply else candidate
        return json.dumps({"steps": [extraction(text)] if text in reply else [], "resolved": []})

    monkeypatch.setattr(h.ask, "run_judge", judge)
    monkeypatch.setattr(h.steps, "aux_llm", extract)
    return cards


def assert_delivery(result, model, *, plain=None):
    assert result["messages"][-1]["content"] == result["final_response"]
    stored = model.agent._session_db.get_messages(model.agent.session_id)
    assert stored[-1]["content"] == result["final_response"]
    assert not model.interims
    # Framing-only controls may stream plain text; retirement must never do so.
    allowed = (result["final_response"], plain) if plain else (result["final_response"],)
    assert model.shown in allowed


@pytest.mark.parametrize("state", ["new", "open", "resolved"])
@pytest.mark.parametrize("framed", [False, True], ids=["plain", "framed"])
def test_exact_source_action_before_publication(harness, monkeypatch, state, framed):
    h = harness
    monkeypatch.setenv("HERMES_USER_STEPS_FRAME", "1" if framed else "0")
    events = []
    cards = wire_models(h, monkeypatch, events)
    ledger = h.steps.core.Ledger()
    sid = h.agent.session_id
    prior_id = None
    if state != "new":
        # Seed through the same real final response pipeline, not a fabricated ledger.
        first = Model(h.agent, monkeypatch, [ASK])
        seeded = h.agent.run_conversation("Pick a case with me.")
        assert_delivery(seeded, first, plain=ASK)
        step = ledger.open_steps(sid)[0]
        prior_id = step["id"]
        assert len(requests(h)) == 1
        assert not cards.calls
        if state == "resolved":
            retire(h, step)
        cards.calls.clear()
        events.clear()

    before = ledger._read(ledger.root(sid))
    canonical_before = requests(h)
    model = Model(h.agent, monkeypatch, [ASK])
    result = h.agent.run_conversation("Continue the case discussion.")
    # Approval readiness previews extraction before the final output transform.
    assert events == [("judge", ASK), ("extract", ASK), ("extract", ASK)]
    after = ledger._read(ledger.root(sid))
    assert len(model.requests) == 1
    if state == "resolved":
        assert result["final_response"] == h.steps.core.HELD_REPLY
        assert ASK not in str(result["messages"])
        assert "::user-step" not in result["final_response"]
        assert [s['id'] for s in after['steps']] == [s['id'] for s in before['steps']]
        assert all(s['status'] == 'resolved' and s['resolved_by'] == 'canonical'
                   for s in after['steps'])
        mutable = {'status', 'resolution', 'resolved_by', 'resolved_at'}
        assert [{k: v for k, v in s.items() if k not in mutable} for s in after['steps']] == [
            {k: v for k, v in s.items() if k not in mutable} for s in before['steps']]
        assert requests(h) == canonical_before
        assert after["gen"] == before["gen"]
        assert ledger.open_steps(sid) == []
        assert cards.calls == []
    else:
        opened = ledger.open_steps(sid)
        assert len(opened) == 1
        if framed:
            assert "::user-step" in result["final_response"]
            assert opened[0]["id"] in result["final_response"]
        else:
            assert result["final_response"] == ASK
        if state == "open":
            assert opened[0]["id"] == prior_id
            assert after["steps"] == before["steps"]
            assert requests(h) == canonical_before
        else:
            published = requests(h)
            assert len(published) == 1
            assert published[0]['requestId'] == opened[0]['requestId']
            assert published[0]['title'] == ASK
            assert published[0]['state'] == 'open'
        assert cards.calls == []  # rolling cards were replaced by canonical requests
    assert_delivery(result, model, plain=None if state == "resolved" else ASK)


def test_held_draft_never_publishes_source_actions(harness, monkeypatch):
    h = harness
    events = []
    cards = wire_models(h, monkeypatch, events, candidate=OTHER, reject_first=True)
    model = Model(h.agent, monkeypatch, [ASK, OTHER])
    result = h.agent.run_conversation("Help me choose a case.")
    assert_delivery(result, model, plain=OTHER)
    # The readiness gate may preview rejected drafts, but only the accepted draft
    # may reach the source-writing transform.
    assert events == [("judge", ASK), ("extract", ASK), ("judge", OTHER),
                      ("extract", OTHER), ("extract", OTHER)]
    assert len(model.requests) == 2
    assert ASK not in model.shown + str(result["messages"]) + str(cards.calls)
    assert ASK not in str(h.db.get_messages(h.agent.session_id))
    assert cards.calls == []
    assert [r['title'] for r in requests(h)] == [OTHER]
    opened = h.steps.core.Ledger().open_steps(h.agent.session_id)
    assert len(opened) == 1 and opened[0]["text"] == OTHER
