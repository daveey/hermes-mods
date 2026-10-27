"""Real discovery, hooks, turn loop, source reconciliation and publication boundary."""
import json
from types import SimpleNamespace

import pytest


ASK = "Please choose the blue or green case for the device."
OTHER = "Please choose the red or yellow case for the device."


def extraction(text=ASK):
    return {"kind": "choose", "text": text, "verbatim_span": text,
            "options": ["blue", "green"] if text == ASK else ["red", "yellow"]}


class Cards:
    """Only external delivery is replaced; production sync_card still runs."""
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
        return json.dumps({"steps": [extraction(candidate)] if candidate in reply else [], "resolved": []})

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
        prior_id = ledger.open_steps(sid)[0]["id"]
        assert len(cards.calls) == 1
        if state == "resolved":
            assert h.steps.core.resolve(ledger, ledger.root(sid), [prior_id], "done", "test") == [prior_id]
        cards.calls.clear()
        events.clear()

    before = ledger._read(ledger.root(sid))
    model = Model(h.agent, monkeypatch, [ASK])
    result = h.agent.run_conversation("Continue the case discussion.")
    assert events == [("judge", ASK), ("extract", ASK)]
    after = ledger._read(ledger.root(sid))
    assert len(model.requests) == 1
    if state == "resolved":
        assert result["final_response"] == h.steps.core.HELD_REPLY
        assert ASK not in str(result["messages"])
        assert "::user-step" not in result["final_response"]
        assert after["steps"] == before["steps"]
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
            assert cards.calls == []
        else:
            assert [call[0] for call in cards.calls] == ["create"]
            assert ASK in cards.calls[0][3]
    assert_delivery(result, model, plain=None if state == "resolved" else ASK)


def test_held_draft_never_reaches_extraction_or_cards(harness, monkeypatch):
    h = harness
    events = []
    cards = wire_models(h, monkeypatch, events, candidate=OTHER, reject_first=True)
    model = Model(h.agent, monkeypatch, [ASK, OTHER])
    result = h.agent.run_conversation("Help me choose a case.")
    assert_delivery(result, model, plain=OTHER)
    assert events == [("judge", ASK), ("judge", OTHER), ("extract", OTHER)]
    assert len(model.requests) == 2
    assert ASK not in model.shown + str(result["messages"]) + str(cards.calls)
    assert ASK not in str(h.db.get_messages(h.agent.session_id))
    assert [call[0] for call in cards.calls] == ["create"]
    opened = h.steps.core.Ledger().open_steps(h.agent.session_id)
    assert len(opened) == 1 and opened[0]["text"] == OTHER
