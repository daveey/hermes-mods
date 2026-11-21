"""The pool is selected for the request's model, so one benched model does not exhaust the whole pool.

2026-11-21: every Anthropic account had a multi-day weekly cooldown on claude-fable-5-2 only. CredentialPool.select()
without a model treats any model cooldown as blocking, and anthropic_messages/codex_responses selected unscoped, so
every Haiku/Sonnet pool call (the session-outcome judge among them) raised PoolExhausted("none available").
"""
import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).parent))
from _load import load  # noqa: E402

pl = load("pool_llm")


class Entry:
    def __init__(self, id, benched):
        self.id, self.label, self.benched = id, f"acct-{id}", set(benched)


class BenchedPool:
    """Mirrors CredentialPool.select: an unscoped select is blocked by ANY model cooldown; a scoped one only by its own."""

    def __init__(self, entries):
        self._entries = entries
        self.selects = []
        self.benched = []

    def entries(self):
        return list(self._entries)

    def select(self, model=None):
        self.selects.append(model)
        for e in self._entries:
            if (model is None and not e.benched) or (model is not None and model not in e.benched):
                return e
        return None

    def mark_exhausted_and_rotate(self, *, credential_id=None, model=None, **_):
        self.benched.append((credential_id, model))
        for e in self._entries:
            if e.id == credential_id:
                e.benched.add(model)
        return None


def fable_benched_pool():
    return BenchedPool([Entry("a", ["claude-fable-5-2"]), Entry("b", ["claude-fable-5-2"])])


def test_anthropic_messages_selects_for_the_request_model(monkeypatch):
    pool = fable_benched_pool()
    used = []

    class Messages:
        def create(self, **body):
            return NS(model_dump=lambda exclude_none=True: {"content": [{"type": "text", "text": "YES"}],
                                                            "model": body["model"]})

    def client(entry):
        used.append(entry.id)
        return NS(messages=Messages())

    monkeypatch.setattr(pl, "anthropic_client", client)
    body = {"model": "claude-haiku-5-5", "max_tokens": 8, "messages": [{"role": "user", "content": "ping"}]}
    resp = pl.anthropic_messages(body, pool=pool)

    assert resp["content"][0]["text"] == "YES"
    assert used == ["a"]
    assert pool.selects == ["claude-haiku-5-5"]


def test_benched_model_itself_still_exhausts(monkeypatch):
    pool = fable_benched_pool()
    monkeypatch.setattr(pl, "anthropic_client", lambda entry: (_ for _ in ()).throw(AssertionError("no account")))
    body = {"model": "claude-fable-5-2", "max_tokens": 8, "messages": [{"role": "user", "content": "ping"}]}
    try:
        pl.anthropic_messages(body, pool=pool)
    except pl.PoolExhausted:
        pass
    else:
        raise AssertionError("a model benched on every account must still raise PoolExhausted")
    assert pool.selects == ["claude-fable-5-2"]


class HTTPErr(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status_code = status


def test_quota_benches_only_the_request_model_then_rotates(monkeypatch):
    pool = fable_benched_pool()
    used = []

    class Messages:
        def __init__(self, entry):
            self.entry = entry

        def create(self, **body):
            used.append(self.entry.id)
            if self.entry.id == "a":
                raise HTTPErr(429, "rate_limit_error: usage limit reached")
            return NS(model_dump=lambda exclude_none=True: {"content": [{"type": "text", "text": "ok"}]})

    monkeypatch.setattr(pl, "anthropic_client", lambda entry: NS(messages=Messages(entry)))
    body = {"model": "claude-haiku-5-5", "max_tokens": 8, "messages": [{"role": "user", "content": "ping"}]}
    assert pl.anthropic_messages(body, pool=pool)["content"][0]["text"] == "ok"
    assert used == ["a", "b"]
    assert pool.benched == [("a", "claude-haiku-5-5")]
    assert pool.selects == ["claude-haiku-5-5", "claude-haiku-5-5"]


def test_codex_responses_selects_for_the_request_model(monkeypatch):
    pool = BenchedPool([Entry("c", ["gpt-6-luna", "gpt-6-sol"]), Entry("d", ["gpt-6-sol"])])
    used = []

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_response(self):
            return NS(model_dump=lambda exclude_none=True: {"output": []})

    def client(entry):
        used.append(entry.id)
        return NS(responses=NS(stream=lambda **b: Stream()))

    monkeypatch.setattr(pl, "codex_client", client)
    pl.codex_responses({"model": "gpt-6-luna", "input": "ping"}, pool=pool)
    assert pool.selects == ["gpt-6-luna"]
    assert used == ["d"]  # c is benched for this model; d is only benched for another one
