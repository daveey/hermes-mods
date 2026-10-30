"""pool_llm.with_rotation over a fixture pool: a 429/credit error moves to the next account; others raise."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _load import load  # noqa: E402

pl = load("pool_llm")


class Entry:
    def __init__(self, id):
        self.id, self.label = id, f"acct-{id}"


class FakePool:
    """Mimics agent.credential_pool.CredentialPool: select() the current account, mark_exhausted_and_rotate()."""

    def __init__(self, ids):
        self._entries = [Entry(i) for i in ids]
        self.exhausted = []

    def entries(self):
        return list(self._entries)

    def select(self, model=None):
        live = [e for e in self._entries if e.id not in self.exhausted]
        return live[0] if live else None

    def mark_exhausted_and_rotate(self, *, status_code, credential_id=None, model=None, error_context=None):
        self.exhausted.append(credential_id)
        return self.select()


class HTTPErr(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status_code = status


def test_rotates_to_next_account_on_429():
    pool = FakePool(["a", "b", "c"])
    seen = []

    def call(entry):
        seen.append(entry.id)
        if entry.id == "a":
            raise HTTPErr(429, "rate_limit_error: usage limit reached")
        return f"ok from {entry.id}"

    assert pl.with_rotation(call, pool) == "ok from b"
    assert seen == ["a", "b"] and pool.exhausted == ["a"]


def test_credit_text_without_status_also_rotates():
    pool = FakePool(["a", "b"])

    def call(entry):
        if entry.id == "a":
            raise RuntimeError("Error code: 400 - Your credit balance is too low to access the Anthropic API")
        return entry.id

    assert pl.with_rotation(call, pool) == "b"


def test_all_accounts_exhausted_raises_pool_exhausted_naming_them():
    pool = FakePool(["a", "b"])

    def call(entry):
        raise HTTPErr(429, "Too Many Requests")

    with pytest.raises(pl.PoolExhausted) as ei:
        pl.with_rotation(call, pool)
    assert "a, b" in str(ei.value) and pool.exhausted == ["a", "b"]


def test_non_quota_error_is_raised_without_rotating():
    pool = FakePool(["a", "b"])

    def call(entry):
        raise HTTPErr(400, "messages: field required")

    with pytest.raises(HTTPErr):
        pl.with_rotation(call, pool)
    assert pool.exhausted == []


def test_oauth_body_adds_claude_code_prefix_and_mcp_tool_names_then_restores():
    body = {"model": "claude-sonnet-5-5", "max_tokens": 10, "system": "Be Eve.",
            "tools": [{"name": "fetch_page", "description": "d", "input_schema": {"type": "object"}}],
            "tool_choice": {"type": "tool", "name": "fetch_page"},
            "messages": [{"role": "user", "content": "hi"},
                         {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "fetch_page",
                                                            "input": {}}]}]}
    wire, names = pl.oauth_messages_body(body)
    assert wire["system"][0]["text"] == pl.CC_PREFIX and wire["system"][1]["text"] == "Be Eve."
    assert wire["tools"][0]["name"] == "mcp__fetch_page"
    assert wire["messages"][1]["content"][0]["name"] == "mcp__fetch_page"
    assert wire["tool_choice"]["name"] == "mcp__fetch_page"
    assert body["tools"][0]["name"] == "fetch_page"  # caller's body untouched
    resp = pl.restore_tool_names({"content": [{"type": "tool_use", "name": "mcp__fetch_page"}]}, names)
    assert resp["content"][0]["name"] == "fetch_page"


def test_codex_body_drops_unsupported_keys_and_forces_store_false():
    b = pl.codex_responses_body({"model": "gpt-6.1-sol", "input": "hi", "max_output_tokens": 9000,
                                 "temperature": 0.2, "store": True, "stream": True})
    assert b == {"model": "gpt-6.1-sol", "input": [{"role": "user", "content": "hi"}], "store": False,
                 "instructions": ""}


def test_provider_aliases():
    assert pl.canonical_provider("openai") == "openai-codex"
    assert pl.canonical_provider("Claude") == "anthropic"
    with pytest.raises(ValueError):
        pl.canonical_provider("bedrock")


def test_complete_passes_thinking_into_the_anthropic_body(monkeypatch):
    # A short classifier answer (session-outcome's YES/NO judge) turns thinking off: adaptive thinking spent the
    # whole max_tokens and the text came back empty.
    sent = []
    monkeypatch.setattr(pl, "resolve_model", lambda provider, model, tier: "claude-haiku-5-5")
    monkeypatch.setattr(pl, "anthropic_messages",
                        lambda body, route=None: sent.append(body) or {"content": [{"type": "text", "text": "NO"}]})
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    r = pl.complete(msgs, tier="fast", max_tokens=8, thinking={"type": "disabled"})
    assert r["text"] == "NO" and sent[-1]["thinking"] == {"type": "disabled"} and sent[-1]["max_tokens"] == 8
    pl.complete(msgs, tier="fast")
    assert "thinking" not in sent[-1]
