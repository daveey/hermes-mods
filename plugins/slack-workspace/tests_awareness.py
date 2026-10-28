"""Offline tests for isolated, same-channel context (no network or live writes)."""
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("sw_awareness_test", HERE / "__init__.py",
                                             submodule_search_locations=[str(HERE)])
assert spec is not None and spec.loader is not None
sw = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = sw
spec.loader.exec_module(sw)
a = sw.awareness
NOW = 2000000000.0


def test_top_level_mention_contains_other_threads_and_bot_replies():
    calls = []
    root = {"ts": str(NOW - 76000), "user": "UA", "text": "Earlier access work", "reply_count": 1}
    reply = {"ts": str(NOW - 33000), "thread_ts": root["ts"], "bot_id": "BE", "text": "GitHub suspended the organization."}
    def api(method, params):
        calls.append((method, params))
        return {"ok": True, "messages": [root] if method.endswith("history") else [root, reply]}
    hook = a.make_hook(api, lambda key: {"CHAT_ID": "CEXAMPLE", "MESSAGE_ID": str(NOW)}.get(key, ""), lambda: NOW)
    text = hook(platform="slack")["context"]
    assert reply["text"] in text
    assert f"thread={root['ts']}" in text
    assert a.CAPABILITIES in text
    assert all(p["channel"] == "CEXAMPLE" for _, p in calls)
    assert len(calls) == 2


def test_pages_history_and_replies_and_filters_current_future_old():
    root = {"ts": str(NOW - 500), "text": "root", "reply_count": 3}
    seen = []
    def api(method, params):
        seen.append((method, params))
        cursor = params.get("cursor")
        if not cursor:
            return {"ok": True, "messages": [root], "response_metadata": {"next_cursor": "next"}}
        if method.endswith("history"):
            return {"ok": True, "messages": [{"ts": str(NOW - 100), "text": "another root"}]}
        return {"ok": True, "messages": [{"ts": str(t), "text": str(t)} for t in (NOW-50, NOW, NOW+1, NOW-90000)]}
    rows, warnings = a.collect(api, "C1", now=NOW, current_ts=str(NOW))
    assert [float(r["ts"]) for r in rows] == [NOW-500, NOW-100, NOW-50]
    assert not warnings
    assert len(seen) == 4


@pytest.mark.parametrize("platform,channel", [("cli", "C1"), ("slack", "D1"), ("slack", "")])
def test_no_cross_surface_or_dm_reads(platform, channel):
    def api(*args):
        pytest.fail("must not read Slack")
    assert a.make_hook(api, lambda key: channel)(platform=platform) is None


def test_errors_are_explicit_without_secret_exception_text():
    def api(*args):
        raise RuntimeError("SECRET")
    text = a.context(api, "C1", now=NOW)
    assert "history is incomplete" in text
    assert "SECRET" not in text
    assert "does not prove" in text


def test_request_budget_and_display_budget(monkeypatch):
    monkeypatch.setattr(a, "MAX_CALLS", 2)
    def api(method, params):
        return {"messages": [{"ts": str(NOW-5), "text": "x"*500}],
                "response_metadata": {"next_cursor": str(int(params.get("cursor", "0"))+1)}}
    rows, warnings = a.collect(api, "C1", now=NOW)
    assert len(rows) == 1
    assert "budget" in " ".join(warnings)
    monkeypatch.setattr(a, "MAX_CHARS", 20)
    assert "Display budget" in a.context(api, "C1", now=NOW)


def test_context_only_registers_no_tools_handlers_and_uses_profile_token(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    root = home / "slack-workspace"
    root.mkdir(parents=True)
    (root / "policy.json").write_text(json.dumps({"context_only": True}))
    (home / ".env").write_text("SLACK_BOT_TOKEN=xoxb-profile-test\n")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-wrong-process-token")
    monkeypatch.setitem(sys.modules, "hermes_constants", types.SimpleNamespace(get_hermes_home=lambda: home))
    hooks = {}
    class Ctx:
        def register_hook(self, name, fn):
            hooks[name] = fn
        def __getattr__(self, key):
            pytest.fail(f"unexpected registration: {key}")
    calls = []
    monkeypatch.setattr(sw.core, "call", lambda token, method, params, **kw: calls.append((token, method, params)) or {"ok": True, "messages": []})
    monkeypatch.setattr(sw, "_session", lambda key: {"CHAT_ID": "C1", "MESSAGE_ID": ""}.get(key, ""))
    sw.register(Ctx())
    assert hooks["pre_llm_call"](platform="slack")["context"]
    assert calls[0][0] == "xoxb-profile-test"
    assert calls[0][1] == "conversations.history"


def test_display_keeps_late_update_after_large_old_thread(monkeypatch):
    old = [{"ts": str(NOW-80000+i), "text": "old "*1000} for i in range(30)]
    latest = {"ts": str(NOW-100), "text": "Organization suspended; transfer is blocked."}
    monkeypatch.setattr(a, "collect", lambda *args, **kw: (old+[latest], []))
    text = a.context(None, "C1", now=NOW)
    assert latest["text"] in text
    assert "Display budget" in text
    assert "message truncated" in text


def test_repeated_cursor_and_missing_cursor_report_incomplete():
    for response in ({"has_more": True}, {"response_metadata": {"next_cursor": "same"}}):
        _, warnings = a.collect(lambda *args: {"messages": [], **response}, "C1", now=NOW)
        assert "incomplete" in " ".join(warnings)
