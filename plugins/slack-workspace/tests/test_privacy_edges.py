"""Edge cases for privacy admission, sharing the isolated regression loader."""
import json

import pytest
from test_privacy import state, sw


@pytest.mark.parametrize("params", [["channel"], "[]", "null", "broken-json"])
def test_non_object_params_refused(state, params):
    assert "error" in json.loads(sw.tool_api(state, {"method": "files.info", "params": params}))


@pytest.mark.parametrize("key", ["file", "file_comment"])
def test_reactions_file_variant_cannot_spoof_channel(state, key):
    state.api = lambda m, p: pytest.fail("private file variant fetched")
    assert "error" in json.loads(sw.tool_api(state, {"method": "reactions.get", "params": {
        "channel": "C1", "timestamp": "1.0", key: "F1"}}))


@pytest.mark.parametrize("method", ["conversations.history", "conversations.replies", "conversations.info",
    "conversations.members", "pins.list", "bookmarks.list", "chat.getPermalink", "reactions.get"])
def test_scoped_reads_refuse_other_private_channel(state, method):
    calls = []
    def api(m, p):
        calls.append(m)
        return {"ok": True, "channel": {"id": "C9", "is_private": True}}
    state.api = api
    assert "error" in json.loads(sw.tool_api(state, {"method": method,
        "params": {"channel": "C9", "timestamp": "1.0"}}))
    assert calls == ["conversations.info"]


@pytest.mark.parametrize("cid", ["C9", "G9", "D9"])
def test_current_private_channel_retains_access(state, cid):
    state.requester = lambda: {"trusted": False, "channel": cid, "user": "U1"}
    state.api = lambda m, p: {"ok": True}
    assert json.loads(sw.tool_api(state, {"method": "conversations.history",
        "params": {"channel": cid}}))["ok"]


@pytest.mark.parametrize("event", ["channel_rename", "group_rename"])
def test_rename_without_name_invalidates_only_target_id(state, event):
    state.store.cache_name("C9", "old", "channel", True)
    state.store.cache_name("C8", "other", "channel", True)
    state.store.record_event({"type": event, "channel": {"id": "C9"}})
    assert state.store.cached("C9") is None
    assert state.store.cached("C8")["private"] == 1


def test_rename_then_lookup_failure_denies(state):
    state.store.cache_name("C9", "old", "channel", True)
    state.store.record_event({"type": "group_rename", "channel": {"id": "C9", "name": "new"}})
    def api(m, p):
        raise sw.core.SlackError(m, "missing_scope")
    state.api = api
    assert not state.can_read("C9", state.requester())[0]
