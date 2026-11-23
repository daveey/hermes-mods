"""Ordered tier fallback uses real rotation, fake registry and fake SDKs; no network."""
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _load import load

pl = load("pool_llm")
FABLE, OPUS = "claude-fable-5-2", "claude-opus-5-5"
MESSAGES = [{"role": "user", "content": "ping"}]


@pytest.fixture
def registry(monkeypatch):
    routes = {
        "deep": [
            {"provider": "anthropic", "model": FABLE, "billing": "subscription"},
            {"provider": "anthropic", "model": "paid-only", "billing": "metered"},
            {"provider": "anthropic", "model": OPUS, "billing": "subscription"},
            {"provider": "anthropic", "model": FABLE, "billing": "subscription"},
            {"provider": "openai-codex", "model": "gpt-6-astra", "billing": "subscription"},
        ],
        "standard": [{"provider": "anthropic", "model": "claude-sonnet-5-5", "billing": "subscription"}],
    }
    routing = ModuleType("agent.model_routing")
    routing.eligible_routes = lambda policy, level: policy["routes"][level]
    managed = ModuleType("agent.model_routing_managed")
    managed.managed_config = lambda: {"model_routing": {"routes": routes}}
    monkeypatch.setitem(sys.modules, "agent.model_routing", routing)
    monkeypatch.setitem(sys.modules, "agent.model_routing_managed", managed)
    return managed


@pytest.fixture
def upstream(monkeypatch):
    calls, blocked = [], set()
    failures = {FABLE}
    entries = [NS(id="fixture-a", label="fixture-a"), NS(id="fixture-b", label="fixture-b")]
    pool = NS(entries=lambda: entries,
              select=lambda model=None: next((e for e in entries if (e.id, model) not in blocked), None),
              mark_exhausted_and_rotate=lambda credential_id=None, model=None, **kw: blocked.add((credential_id, model)))

    def client(entry):
        def create(**body):
            model = body["model"]
            calls.append((entry.id, model))
            if model in failures:
                exc = RuntimeError("credits_required: requires usage credits")
                exc.status_code = 429
                raise exc
            return NS(model_dump=lambda **kw: {"content": [{"type": "text", "text": "pong"}], "stop_reason": "end_turn"})
        return NS(messages=NS(create=create))

    monkeypatch.setattr(pl, "load_pool", lambda provider: pool)
    monkeypatch.setattr(pl, "anthropic_client", client)
    monkeypatch.setattr(pl, "codex_responses", lambda *a, **kw: pytest.fail("cross-provider fallback"))
    return calls, failures


def test_resolve_secondary_subscription_model(registry):
    assert pl.resolve_model("anthropic", OPUS) == OPUS
    with pytest.raises(ValueError):
        pl.resolve_model("anthropic", "paid-only")


def test_routes_order_dedup_and_legacy_pins(registry):
    assert pl.managed_routes()[("anthropic", "deep")] == [FABLE, OPUS]
    assert pl.managed_pins()[("anthropic", "deep")] == FABLE


def test_complete_credit_wall_falls_back(registry, upstream):
    calls, _ = upstream
    result = pl.complete(MESSAGES, tier="deep")
    assert result["model"] == OPUS
    assert result["fallback_from"] == FABLE
    assert result["text"] == "pong"
    assert result["route"]["model"] == OPUS
    assert [m for _, m in calls] == [FABLE, FABLE, OPUS]


@pytest.mark.parametrize("entrypoint", ["complete", "client", "chat"])
def test_explicit_model_never_falls_back(registry, upstream, entrypoint):
    calls, _ = upstream
    with pytest.raises(pl.PoolExhausted):
        if entrypoint == "complete":
            pl.complete(MESSAGES, model=FABLE)
        elif entrypoint == "client":
            pl.client(model=FABLE).chat.completions.create(messages=MESSAGES)
        else:
            pl._chat_completions({"model": FABLE, "messages": MESSAGES}, {})
    assert [m for _, m in calls] == [FABLE, FABLE]


def test_all_models_exhausted_names_each(registry, upstream):
    calls, failures = upstream
    failures.add(OPUS)
    with pytest.raises(pl.PoolExhausted) as caught:
        pl.complete(MESSAGES, tier="deep")
    assert FABLE in str(caught.value) and OPUS in str(caught.value)
    assert [m for _, m in calls] == [FABLE, FABLE, OPUS, OPUS]


def test_defaults_and_codex_unchanged(registry, monkeypatch):
    assert pl.resolve_model("codex", tier="deep") == "gpt-6-astra"
    registry.managed_config = lambda: None
    assert pl.managed_pins() is None
    for (provider, tier), model in pl.DEFAULT_MODELS.items():
        assert pl.resolve_model(provider, tier=tier) == model
    assert pl.resolve_model("anthropic", "legacy-custom") == "legacy-custom"


@pytest.mark.parametrize("json_output", [False, True])
def test_ask_fallback_notice(registry, upstream, capsys, json_output):
    assert pl.main(["ask", "ping", "--tier", "deep"] + (["--json"] if json_output else [])) == 0
    out = capsys.readouterr()
    assert len(out.err.splitlines()) == 1
    assert FABLE + " refused (" in out.err and "served by " + OPUS in out.err
    if json_output:
        result = json.loads(out.out)
        assert result["model"] == OPUS and result["fallback_from"] == FABLE
    else:
        assert out.out == "pong\n"


def test_models_lists_all_routes_in_order(registry, capsys):
    assert pl.main(["models"]) == 0
    lines = [line.split() for line in capsys.readouterr().out.splitlines()]
    assert [m for p, tier, m in lines if p == "anthropic" and tier == "deep"] == [FABLE, OPUS]
    assert not any("paid-only" in line for line in lines)


def test_non_pool_errors_do_not_fall_back(registry, monkeypatch):
    calls = []
    def fail(body, **kw):
        calls.append(body["model"])
        raise ValueError("invalid request")
    monkeypatch.setattr(pl, "anthropic_messages", fail)
    with pytest.raises(ValueError, match="invalid request"):
        pl.complete(MESSAGES, tier="deep")
    assert calls == [FABLE]
