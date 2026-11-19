"""Use the core wake/replay path and its exception-derived build-failure record."""
from types import SimpleNamespace

import pytest
from test_relay_retry import setup, plugin


@pytest.mark.parametrize('raised', [False, True])
@pytest.mark.parametrize('relay', [False, True])
@pytest.mark.parametrize('attempt', [0, 1, 2, 3])
def test_build_quota_records_quota_even_after_automation_replay(tmp_path, monkeypatch, relay, attempt, raised):
    pytest.importorskip('hermes_cli', reason='requires core wake implementation')
    from tui_gateway import server, automation_replay
    from agent.model_routing_unavailable import SubscriptionQuotaUnavailable
    led, _, _ = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(plugin, '_state', {})
    now = 1000.0
    error = SubscriptionQuotaUnavailable('openai-codex', 'gpt-x', now + 1800)
    session = {}
    def build(*args):
        # The build worker records the exception's reset for wake/replay consumers.
        session.update(agent_error=str(error), agent_failure={'failure_resets_at': error.resets_at})
    monkeypatch.setattr(server, '_find_live_session_by_key', lambda _: ('live', session))
    monkeypatch.setattr(server, '_restart_completed_failed_agent_build', lambda *args: None)
    monkeypatch.setattr(server, '_start_agent_build', build)
    monkeypatch.setattr(server, '_wait_agent', lambda *args, **kwargs: bool(session.get('agent_error')))
    monkeypatch.setattr(plugin, '_server', lambda: server)
    monkeypatch.setattr(plugin, '_quota_wall', lambda *args: pytest.fail('must use the build exception, not pool'))
    scheduled = []
    monkeypatch.setattr(automation_replay, 'schedule', lambda *a, **kw: scheduled.append(kw))
    monkeypatch.setattr(automation_replay.time, 'time', lambda: now)
    def wake(sid, text, **kwargs):
        if raised:
            raise error
        kwargs['label'] = 'automation-replay' if attempt else 'chat-closeout'
        if attempt:
            kwargs['display_metadata'] = {
                **kwargs.get('display_metadata', {}),
                automation_replay.REPLAY_METADATA_KEY: {'attempt': attempt, 'failed_at': 900.0},
            }
        return server._wake_session_turn(sid, text, **kwargs)
    if relay:
        from test_quota_wall import ASK
        cards = ASK
    else:
        cards = {'work': {'status': 'done'}}
        sig = plugin.core.closeout_key({'batch': ['work']}, cards)
        led.write('chat', batch=['work'], settled_sig=sig, settled_at=1)
    result = plugin.wake_tick(now=now, wake=wake, unseen={}, last_user=lambda _: 0,
                              read=lambda _: cards, proof=lambda *args: False)
    assert result[0]['result'] == 'quota'
    row = led.get('chat')
    assert row['wake_result'] == 'quota' and row['retry_after'] == error.resets_at
    # A still-active quota wall re-arms early replays, but the third is final.
    assert len(scheduled) == (0 if attempt == 3 or raised else 1)
    if scheduled:
        assert scheduled[0]['due'] == error.resets_at
        assert scheduled[0]['persisted'] is False
        if attempt:
            assert scheduled[0]['attempt'] == attempt
            assert scheduled[0]['failed_at'] == 900.0
            assert automation_replay.REPLAY_METADATA_KEY not in scheduled[0]['display_metadata']


@pytest.mark.parametrize('reset', [None, 999, True, float('inf'), float('nan'), '2800'])
def test_invalid_or_expired_build_reset_does_not_hide_error(tmp_path, monkeypatch, reset):
    led, _, tick = setup(tmp_path, monkeypatch, 'error')
    session = {'agent_error': 'failed', 'agent_failure': {'failure_resets_at': reset}}
    monkeypatch.setattr(plugin, '_server', lambda: SimpleNamespace(_find_live_session_by_key=lambda _: ('live', session)))
    monkeypatch.setattr(plugin, '_quota_wall', lambda *args: None)
    tick(1000)
    assert led.get('chat')['wake_result'] == 'error'
