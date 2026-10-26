"""Post-fix independent behavioral checks for the supported native process path."""
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("run18_adapter_fixture", Path(__file__).with_name("test_goal_process_adapter.py"))
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
consumer = _fixture.consumer
owned = _fixture.owned
gd = _fixture.gd


@pytest.mark.parametrize("refusal", [False, None, "exception"])
def test_live_poller_retries_refusal_and_records_actual_success(consumer, tmp_path, monkeypatch, refusal):
    from tui_gateway import server
    from tests.waiting import wait_until
    health, registry, session, state, stop, complete = consumer
    submit = server._run_prompt_submit
    attempts = []

    def once(*args, **kwargs):
        attempts.append(args[3])
        if len(attempts) == 1:
            session["running"] = False
            state["submitted"].set()
            if refusal == "exception":
                raise RuntimeError("one transient admission failure")
            return refusal
        return submit(*args, **kwargs)

    monkeypatch.setattr(server, "_run_prompt_submit", once)
    complete()
    wait_until(lambda: health.available(tmp_path, "origin", registry.ownership_generation), timeout=5)
    stop.set()
    for event, thread in server._notification_pollers:
        if event is stop:
            thread.join(5)
            assert not thread.is_alive()
    assert len(attempts) == 2
    assert attempts[0] == attempts[1]
    assert registry.completion_queue.empty()


def test_new_canonical_user_rejects_for_scope_not_mock_signature(owned, tmp_path, monkeypatch):
    v, evidence, _, c = owned
    health = c[0]
    original = health.available
    calls = []

    def available(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(result)
        if len(calls) == 1:
            from hermes_state import SessionDB
            db = SessionDB(tmp_path / "state.db")
            db.append_message("origin", "user", "Stop repairing", message_uid="run18-new-human")
            db.close()
        return result

    monkeypatch.setattr(health, "available", available)
    with pytest.raises(ValueError, match="^owner_process_current_request_mismatch$"):
        gd.validate_disposition(v, evidence, session_id="origin")
    assert calls == [True]


def test_stop_epoch_rejects_even_when_cancel_latch_clears(owned, monkeypatch):
    from tui_gateway import server
    v, evidence, _, c = owned
    _, registry, session, *_ = c
    original = registry.ownership_snapshot
    calls = []

    def read(handle):
        result = original(handle)
        calls.append(result)
        if len(calls) == 2:
            server._interrupt_session_turn("ui", session)
            session["_turn_cancel_requested"] = False
        return result

    monkeypatch.setattr(registry, "ownership_snapshot", read)
    with pytest.raises(ValueError, match="^owner_process_consumer_changed$"):
        gd.validate_disposition(v, evidence, session_id="origin")
    assert len(calls) == 2
    assert calls[0] == calls[1]
