"""Source-review privacy regressions; no live Slack or Hermes state."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("privacy_sw", HERE / "__init__.py",
                                             submodule_search_locations=[str(HERE)])
sw = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = sw
spec.loader.exec_module(sw)


@pytest.fixture
def state(tmp_path):
    st = sw.State(tmp_path)
    st.requester = lambda: {"trusted": False, "channel": "C1", "user": "U1"}
    return st


@pytest.mark.parametrize("method", ["search.messages", "search.all", "search.files",
    "assistant.search.context", "files.info", "files.list", "reactions.list", "reactions.get",
    "canvases.sections.lookup", "conversations.list", "users.conversations", "reminders.list"])
@pytest.mark.parametrize("params", [{}, {"channel": "C1"}, {"channels": "C1,G9"}])
def test_channel_less_content_denied_before_fetch(state, method, params):
    calls = []
    state.api = lambda m, p: calls.append(m) or {"ok": True, "secret": "private content"}
    out = json.loads(sw.tool_api(state, {"method": method, "params": params}))
    # reactions.get has both message and file variants; only message arguments are scoped.
    assert "error" in out
    assert calls == []


@pytest.mark.parametrize("event", ["channel_rename", "group_rename", "channel_created"])
@pytest.mark.parametrize("cached", [False, True])
def test_rename_rechecks_privacy_by_id(state, event, cached):
    if cached:
        state.store.cache_name("C9", "old", "channel", False)
    state.store.record_event({"type": event, "channel": {"id": "C9", "name": "new"}})
    calls = []
    def api(method, params):
        calls.append((method, params))
        return {"ok": True, "channel": {"id": "C9", "name": "new", "is_private": True}}
    state.api = api
    assert state.can_read("C9", state.requester())[0] is False
    assert calls == [("conversations.info", {"channel": "C9"})]


def test_unknown_channel_is_not_public(state):
    def api(method, params):
        raise sw.core.SlackError(method, "channel_not_found")
    state.api = api
    assert state.can_read("C9", state.requester())[0] is False


@pytest.mark.parametrize("method", ["files.getUploadURLExternal", "files.getSecretFuture", "search.future"])
def test_read_prefix_does_not_override_semantics(method):
    assert sw.core.method_tier(method) == (sw.core.ACT if method == "files.getUploadURLExternal" else sw.core.ADMIN)


@pytest.mark.parametrize("method,params", [
    ("conversations.history", {"channel": "C1"}),
    ("conversations.history", {"channel": "C2"}),
    ("reactions.get", {"channel": "C1", "timestamp": "1.0"}),
    ("users.info", {"user": "U1"}),
])
def test_safe_reads_still_work(state, method, params):
    state.api = lambda m, p: {"ok": True, "channel": {"id": "C2", "is_private": False}}
    assert json.loads(sw.tool_api(state, {"method": method, "params": params}))["ok"]


@pytest.mark.parametrize("params", [{}, {"channel": "G9"}, {"channel": "C1,C2"}, {"channel": "#general"},
    {"channel_id": "C1"}, {"channel": "C1", "channel_id": "G9"},
    {"channel": "C1", "channels": ["C1", "G9"]}])
def test_channel_reads_require_unambiguous_verified_id(state, params):
    state.api = lambda m, p: {"ok": True, "channel": {"id": "G9", "is_private": True}}
    assert "error" in json.loads(sw.tool_api(state, {"method": "conversations.history", "params": params}))


def test_trusted_channel_less_read_retained(state):
    state.requester = lambda: {"trusted": True, "channel": "", "user": "admin"}
    state.api = lambda m, p: {"ok": True}
    assert json.loads(sw.tool_api(state, {"method": "files.info", "params": {"file": "F1"}}))["ok"]
