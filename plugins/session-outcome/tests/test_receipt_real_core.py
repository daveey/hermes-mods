"""Real tool → canonical writer receipt round trips on disposable state."""
import json

import pytest
import test_real_core as helpers
from test_completion_receipt import receipt

real = helpers.real


@pytest.mark.parametrize("disposition", ["delivered", "rejected", "superseded", "contained"])
def test_tool_persists_receipt_without_upgrading_evidence(real, disposition):
    so, plugin = real
    value = receipt(disposition)
    result = json.loads(plugin.handle({"kind": "done", "summary": "Bounded assessment complete.",
                                      "completion_receipt": value}, session_id="chat"))
    assert result["ok"] and result["declared"]
    stored = so.get_outcome("chat")
    assert stored["completion_receipt"] == value
    source = stored["completion_receipt_source"]
    assert source["kind"] == "session_declaration"
    refreshed = so.refresh_outcome("chat", "manual")
    assert refreshed["completion_receipt"] == value
    assert refreshed["completion_receipt_source"] == source


def test_tool_preserves_deferred_scope_without_authorizing_execution(real):
    so, plugin = real
    value = receipt()
    value["items"][0]["remaining"] = [{"scope": "enable enforcement", "state": "deferred",
                                        "owner": None, "reason": "User deferred; no execution authorized."}]
    result = json.loads(plugin.handle({"kind": "done", "summary": "Report delivered, enforcement deferred.",
                                      "completion_receipt": value}, session_id="chat"))
    assert result["declared"]
    assert so.get_outcome("chat")["completion_receipt"] == value


@pytest.mark.parametrize("bad", [None, {}, {"version": 1, "items": []}])
def test_malformed_tool_receipt_does_not_change_existing_outcome(real, bad):
    so, plugin = real
    so.declare_outcome("chat", {"kind": "done", "summary": "Previous bounded result."})
    previous = so.get_outcome("chat")
    result = json.loads(plugin.handle({"kind": "done", "summary": "Invalid change.", "completion_receipt": bad}, session_id="chat"))
    assert not result["ok"]
    assert so.get_outcome("chat") == previous


def test_tool_pending_owner_is_not_admitted_by_structural_validation(real):
    so, plugin = real
    value = receipt()
    value["items"][0]["remaining"] = [{"scope": "remaining repair", "state": "pending",
                                        "owner": "@task:default@unknown/t_missing", "reason": "Must still repair."}]
    previous = so.get_outcome("chat")
    result = json.loads(plugin.handle({"kind": "done", "summary": "Bounded assessment complete.", "completion_receipt": value}, session_id="chat"))
    assert not result["ok"]
    assert so.get_outcome("chat") == previous
