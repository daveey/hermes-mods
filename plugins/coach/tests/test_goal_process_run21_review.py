"""Independent R4 source replacement matrix; no network/model."""
import importlib.util
from pathlib import Path
import pytest
from gateway.process_consumer_health import receipt_version
from gateway.session_identity import replace_source
from tests.gateway.test_process_consumer_health import owned, health


def public_args(owned):
    spec = importlib.util.spec_from_file_location("run21_public_goal", Path(__file__).with_name("test_goal_process_gateway.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.gd, module.args(owned)


@pytest.mark.asyncio
@pytest.mark.parametrize("timing", ["admission", "readback"])
@pytest.mark.parametrize("field,value", [
    ("thread_id", "foreign-thread"), ("user_id", "foreign-user"),
    ("profile", "foreign-profile"), ("scope_id", "foreign-scope"),
    ("delivered_via_upstream_relay", True),
])
async def test_route_component_replacement_fails_closed(owned, monkeypatch, timing, field, value):
    runner, adapter, registry, process, entry, event, _ = owned
    original = adapter.handle_message
    async def replace_after_real_acceptance(message):
        await original(message)
        assert message._gateway_accepted is True
        message.source = replace_source(message.source, **{field: value})
    if timing == "admission":
        monkeypatch.setattr(adapter, "handle_message", replace_after_real_acceptance)
    assert await runner._inject_watch_notification("run21 fixture", event) is True
    pending = adapter.get_pending_message(entry.session_key)
    assert pending._gateway_accepted is True
    snapshot = registry.ownership_snapshot(process.id)
    if timing == "readback":
        version = health(owned)
        assert version is not None
        pending.source = replace_source(pending.source, **{field: value})
        assert receipt_version(snapshot, expected=version) is None
    assert getattr(pending.source, field) == value
    gd, (v, evidence, sid) = public_args(owned)
    with pytest.raises(ValueError):
        gd.validate_disposition(v, evidence, session_id=sid)
    assert health(owned) is None


@pytest.mark.asyncio
async def test_repeated_equivalent_copies_and_nonrouting_change(owned, monkeypatch):
    runner, adapter, _, _, entry, event, _ = owned
    def prime(message):
        message.source = replace_source(message.source)
    monkeypatch.setattr(adapter, "prime_routing_cache", prime, raising=False)
    assert await runner._inject_watch_notification("run21 fixture", event) is True
    version = health(owned)
    assert version is not None
    pending = adapter.get_pending_message(entry.session_key)
    for _ in range(3):
        previous = pending.source
        pending.source = replace_source(previous, message_id="display-only-anchor")
        assert pending.source is not previous
        assert health(owned) == version
        gd, (v, evidence, sid) = public_args(owned)
        assert gd.validate_disposition(v, evidence, session_id=sid)["disposition"] == "owned_continuing"
