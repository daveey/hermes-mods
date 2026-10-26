"""Independent regressions for native ownership proof and completion retention."""
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("review_adapter_fixture", Path(__file__).with_name("test_goal_process_adapter.py"))
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
consumer = _fixture.consumer
owned = _fixture.owned
gd = _fixture.gd


def test_stop_between_consumer_proof_and_final_snapshot_rejects(owned, monkeypatch):
    v, evidence, process, c = owned
    health, registry, session, *_ = c
    original = registry.ownership_snapshot
    reads = []

    def read(handle):
        result = original(handle)
        reads.append(handle)
        if len(reads) == 2:
            # The native Stop epoch changes after available(), before final read returns.
            from tui_gateway import server
            server._interrupt_session_turn("ui", session)
        return result

    monkeypatch.setattr(registry, "ownership_snapshot", read)
    with pytest.raises(ValueError):
        gd.validate_disposition(v, evidence, session_id="origin")


def test_new_canonical_user_during_consumer_read_rejects(owned, tmp_path, monkeypatch):
    v, evidence, _, c = owned
    health = c[0]
    original = health.available

    def available(*args):
        result = original(*args)
        from hermes_state import SessionDB
        db = SessionDB(tmp_path / "state.db")
        db.append_message("origin", "user", "Stop repairing", message_uid="new-human-message")
        db.close()
        return result

    monkeypatch.setattr(health, "available", available)
    with pytest.raises(ValueError):
        gd.validate_disposition(v, evidence, session_id="origin")


@pytest.mark.parametrize("outcome", [False, None, "exception"])
def test_refused_native_completion_remains_retryable(consumer, monkeypatch, outcome):
    import sys
    from gateway.session_context import scoped_current_session_id
    from tui_gateway import server
    from tools.process_registry_notifications import format_process_notification

    health, registry, session, state, stop, complete = consumer
    complete()
    stop.set()
    for event, thread in server._notification_pollers:
        if event is stop:
            thread.join(5)
            assert not thread.is_alive()
    with scoped_current_session_id("origin"):
        process = registry.spawn_local(
            f"exec {sys.executable} -c 'pass'", session_key="origin")
        process.notify_on_complete = True
    assert process._completion_event.wait(5)
    event = registry.completion_queue.get(timeout=5)
    assert event["session_id"] == process.id
    attempts = []

    def refuse(*args, **kwargs):
        attempts.append(args)
        session["running"] = False
        if outcome == "exception":
            raise RuntimeError("transient fixture admission failure")
        return outcome

    monkeypatch.setattr(server, "_run_prompt_submit", refuse)
    deferred = []
    server._notif_dispatch_completions("ui", session,
        [(event, format_process_notification(event))], registry, deferred)
    assert len(attempts) == 1
    assert deferred or not registry.completion_queue.empty(), (
        "Refused native completion disappeared: delegation claim release does not requeue process events")

    retry_events = list(deferred)
    while not registry.completion_queue.empty():
        retry_events.append(registry.completion_queue.get_nowait())
    assert retry_events == [event]
    monkeypatch.setattr(server, "_run_prompt_submit", lambda *a, **kw: True)
    server._notif_dispatch_completions("ui", session,
        [(event, format_process_notification(event))], registry, [])
    assert registry.completion_queue.empty()
