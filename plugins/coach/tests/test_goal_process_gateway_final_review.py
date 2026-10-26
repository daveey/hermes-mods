"""Final receipt interleavings using native isolated fixture; no network."""
import importlib.util
from pathlib import Path
import pytest
from gateway.session_identity import replace_source
from tests.gateway.test_process_consumer_health import owned, health


def reject_public(owned):
    path = Path(__file__).with_name("test_goal_process_gateway_review.py")
    spec = importlib.util.spec_from_file_location("final_review_helpers", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.assert_public_gate_rejects(owned)


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["origin", "source_replacement"])
async def test_admission_identity_changes(owned, monkeypatch, drift):
    runner, adapter, _, _, entry, event, _ = owned
    original = adapter.handle_message
    async def change_after_acceptance(message):
        await original(message)
        assert message._gateway_accepted is True
        if drift == "origin":
            entry.origin = replace_source(entry.origin, chat_id="changed-chat")
        else:
            message.source = replace_source(message.source, chat_id="changed-chat")
    monkeypatch.setattr(adapter, "handle_message", change_after_acceptance)
    assert await runner._inject_watch_notification("fixture", event) is True
    if drift == "source_replacement":
        pending = adapter.get_pending_message(entry.session_key)
        assert pending.source.chat_id == "changed-chat"
    reject_public(owned)
    assert health(owned) is None


@pytest.mark.asyncio
async def test_failed_adapter_lookup_revokes_success(owned, monkeypatch):
    runner, _, _, _, _, event, _ = owned
    assert await runner._inject_watch_notification("fixture", event) is True
    assert health(owned) is not None
    with monkeypatch.context() as patch:
        patch.setattr(runner, "_resolve_injection_adapter", lambda *args: None)
        assert await runner._inject_watch_notification("fixture", event) is False
    reject_public(owned)
    assert health(owned) is None


@pytest.mark.asyncio
async def test_newer_failed_attempt_blocks_older_success(owned, monkeypatch):
    runner, adapter, _, _, _, event, _ = owned
    original = adapter.handle_message
    async def nested_failure(message):
        await original(message)
        assert message._gateway_accepted is True
        with monkeypatch.context() as patch:
            patch.setattr(runner, "_build_process_event_source", lambda evt: None)
            assert await runner._inject_watch_notification("newer fixture", event) is None
    monkeypatch.setattr(adapter, "handle_message", nested_failure)
    assert await runner._inject_watch_notification("older fixture", event) is True
    reject_public(owned)
    assert health(owned) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("timing", ["admission", "readback"])
async def test_equivalent_source_copy_preserves_public_receipt(owned, monkeypatch, timing):
    runner, adapter, _, _, entry, event, _ = owned
    original = adapter.handle_message
    async def copy_after_acceptance(message):
        await original(message)
        assert message._gateway_accepted is True
        source = message.source
        message.source = replace_source(source)
        assert message.source is not source
    if timing == "admission":
        monkeypatch.setattr(adapter, "handle_message", copy_after_acceptance)
    assert await runner._inject_watch_notification("fixture", event) is True
    version = health(owned)
    assert version is not None
    pending = adapter.get_pending_message(entry.session_key)
    assert pending._gateway_accepted is True
    if timing == "readback":
        source = pending.source
        pending.source = replace_source(source)
        assert pending.source is not source
    assert health(owned) == version
    path = Path(__file__).with_name("test_goal_process_gateway.py")
    spec = importlib.util.spec_from_file_location("copy_gateway_goal", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    v, evidence, sid = module.args(owned)
    assert module.gd.validate_disposition(v, evidence, session_id=sid)["disposition"] == "owned_continuing"


@pytest.mark.asyncio
async def test_late_source_replacement_invalidates_published_receipt(owned):
    from gateway.process_consumer_health import receipt_version
    runner, adapter, registry, process, entry, event, _ = owned
    assert await runner._inject_watch_notification("fixture", event) is True
    snapshot = registry.ownership_snapshot(process.id)
    version = receipt_version(snapshot)
    assert version is not None
    pending = adapter.get_pending_message(entry.session_key)
    assert pending._gateway_accepted is True
    pending.source = replace_source(pending.source, chat_id="changed-chat")
    reject_public(owned)
    assert receipt_version(snapshot, expected=version) is None
    assert health(owned) is None
