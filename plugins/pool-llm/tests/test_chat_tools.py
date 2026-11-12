"""OpenAI chat tool protocol at the serve boundary (no provider calls)."""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _load import load

pl = load("pool_llm")
TOOL = {"type": "function", "function": {"name": "weather", "description": "Read weather",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}}


@pytest.fixture
def upstream(monkeypatch):
    seen = {}
    reply = {"id": "msg_test", "model": "claude-test", "content": [
        {"type": "tool_use", "id": "call_1", "name": "weather", "input": {"city": "Paris"}}],
        "stop_reason": "tool_use", "usage": {"input_tokens": 12, "output_tokens": 7}}
    def send(body, *, route):
        seen.update(body)
        route.update(provider="anthropic", account="fixture")
        return reply
    monkeypatch.setattr(pl, "resolve_model", lambda provider, model=None, tier=None: model or "gpt-test")
    monkeypatch.setattr(pl, "anthropic_messages", send)
    return seen, reply


def request(**extra):
    return {"model": "claude-test", "messages": [{"role": "user", "content": "Weather?"}],
            "tools": [copy.deepcopy(TOOL)], **extra}


@pytest.mark.parametrize("choice,expected", [("auto", {"type": "auto"}), ("none", {"type": "none"}),
    ("required", {"type": "any"}), ({"type": "function", "function": {"name": "weather"}},
                                    {"type": "tool", "name": "weather"})])
def test_request_translation(upstream, choice, expected):
    seen, _ = upstream
    body = request(tool_choice=choice, max_completion_tokens=99)
    body["messages"].insert(0, {"role": "system", "content": "Be brief"})
    before = copy.deepcopy(body)
    pl._chat_completions(body, {})
    assert seen["tools"] == [{"name": "weather", "description": "Read weather",
                              "input_schema": TOOL["function"]["parameters"]}]
    assert seen["tool_choice"] == expected
    assert seen["system"] == "Be brief" and seen["max_tokens"] == 99
    assert body == before


@pytest.mark.parametrize("thinking", [{"type": "disabled"}, {"type": "enabled", "budget_tokens": 1024}, None])
def test_thinking_passthrough(upstream, thinking):
    seen, _ = upstream
    body = request(thinking=thinking)
    before = copy.deepcopy(body)
    pl._chat_completions(body, {})
    assert "thinking" in seen
    assert seen["thinking"] == thinking
    assert body == before


def test_absent_thinking_is_not_added(upstream):
    seen, _ = upstream
    pl._chat_completions(request(), {})
    assert "thinking" not in seen


def test_reply_calls_and_usage(upstream):
    route = {}
    out = pl._chat_completions(request(), route)
    assert out["choices"][0] == {"index": 0, "finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function",
        "function": {"name": "weather", "arguments": '{"city": "Paris"}'}}]}}
    assert out["usage"] == {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19}
    assert route == {"provider": "anthropic", "account": "fixture"}


@pytest.mark.parametrize("thinking_tokens", [1191, 0])
def test_reply_thinking_tokens_reported_as_reasoning(upstream, thinking_tokens):
    _, reply = upstream
    reply["usage"]["output_tokens"] = 1500
    reply["usage"]["output_tokens_details"] = {"thinking_tokens": thinking_tokens}
    before = copy.deepcopy(reply)
    out = pl._chat_completions(request(), {})
    assert out["usage"] == {"prompt_tokens": 12, "completion_tokens": 1500,
                            "total_tokens": 1512,
                            "completion_tokens_details": {"reasoning_tokens": thinking_tokens}}
    assert out["choices"][0]["finish_reason"] == "tool_calls"
    assert reply == before


def test_reply_without_thinking_tokens_omits_reasoning(upstream):
    _, reply = upstream
    reply["usage"]["output_tokens_details"] = {}
    out = pl._chat_completions(request(), {})
    assert "completion_tokens_details" not in out["usage"]


@pytest.mark.parametrize("reason,finish", [("max_tokens", "length"), ("end_turn", "stop")])
def test_text_reply(upstream, reason, finish):
    _, reply = upstream
    reply.update(content=[{"type": "text", "text": "one"}, {"type": "text", "text": "two"}], stop_reason=reason)
    out = pl._chat_completions(request(), {})["choices"][0]
    assert out["finish_reason"] == finish
    assert out["message"] == {"role": "assistant", "content": "onetwo"}


def test_multi_turn_results_even_without_redeclaring_tools(upstream):
    seen, _ = upstream
    body = request()
    body.pop("tools")
    body["messages"] += [{"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "weather", "arguments": '{"city":"Paris"}'}},
        {"id": "call_2", "type": "function", "function": {"name": "weather", "arguments": '{"city":"Rome"}'}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "sunny"},
        {"role": "tool", "tool_call_id": "call_2", "content": "rain"}]
    pl._chat_completions(body, {})
    assert seen["messages"][1] == {"role": "assistant", "content": [
        {"type": "tool_use", "id": "call_1", "name": "weather", "input": {"city": "Paris"}},
        {"type": "tool_use", "id": "call_2", "name": "weather", "input": {"city": "Rome"}}]}
    assert seen["messages"][2:] == [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "call_1", "content": "sunny"},
        {"type": "tool_result", "tool_use_id": "call_2", "content": "rain"}]}]


@pytest.mark.parametrize("model", ["claude-test", "gpt-test"])
def test_no_tools_unchanged(monkeypatch, model):
    seen = {}
    def complete(messages, **kwargs):
        seen.update(kwargs)
        return {"model": model, "text": "legacy", "route": {"account": "fixture"}}
    monkeypatch.setattr(pl, "complete", complete)
    route = {}
    out = pl._chat_completions({"model": model, "messages": [], "max_tokens": 23}, route)
    assert out == {"id": "pool-chat", "object": "chat.completion", "model": model,
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "legacy"}}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}
    assert seen["max_tokens"] == 23 and route == {"account": "fixture"}


@pytest.mark.parametrize("parallel", [True, False])
def test_parallel_and_cached_usage(upstream, parallel):
    seen, reply = upstream
    reply["usage"].update(cache_creation_input_tokens=10, cache_read_input_tokens=20)
    out = pl._chat_completions(request(parallel_tool_calls=parallel), {})
    assert seen["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": not parallel}
    assert out["usage"] == {"prompt_tokens": 42, "completion_tokens": 7, "total_tokens": 49}


@pytest.mark.parametrize("messages", [[None], [{"role": "user", "content": ""}],
    [{"role": "tool", "tool_call_id": "a", "content": {}}],
    [{"role": "user", "content": [{"type": "image_url"}]}], [{"role": "other", "content": "a"}],
    [{"role": "assistant", "tool_calls": [{"type": "function", "id": "a",
       "function": {"name": "weather", "arguments": "not json"}}]}]])
def test_invalid_history_rejected(upstream, messages):
    with pytest.raises(ValueError):
        pl._chat_completions(request(messages=messages), {})


def test_unsupported_tool_type(upstream):
    with pytest.raises(ValueError, match="only function"):
        pl._chat_completions(request(tools=[{"type": "custom"}]), {})


def test_developer_and_text_blocks(upstream):
    seen, _ = upstream
    pl._chat_completions(request(messages=[{"role": "developer", "content": "brief"},
        {"role": "user", "content": [{"type": "text", "text": "Weather?"}]}]), {})
    assert seen["system"] == "brief"
    assert seen["messages"] == [{"role": "user", "content": [{"type": "text", "text": "Weather?"}]}]


@pytest.mark.parametrize("tools", [None, []])
def test_empty_tools_are_not_forwarded(upstream, tools):
    seen, _ = upstream
    pl._chat_completions(request(tools=tools), {})
    assert "tools" not in seen


def test_codex_tools_http_400(monkeypatch):
    import http.server
    import threading
    import urllib.request
    import urllib.error
    servers = []
    original = http.server.ThreadingHTTPServer
    def server(address, handler):
        srv = original(("127.0.0.1", 0), handler)
        servers.append(srv)
        return srv
    monkeypatch.setattr(http.server, "ThreadingHTTPServer", server)
    monkeypatch.setattr(pl, "complete", lambda *a, **kw: pytest.fail("must not call text backend"))
    thread = threading.Thread(target=pl.serve, daemon=True)
    thread.start()
    import time
    for _ in range(100):
        if servers:
            break
        time.sleep(.01)
    srv = servers[0]
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{srv.server_port}/v1/chat/completions",
            data=json.dumps(request(model="gpt-test")).encode(), headers={"Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(req, timeout=5)
        assert error.value.code == 400
        assert json.load(error.value)["error"]["message"] == "tools are not supported on this route"
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(5)
