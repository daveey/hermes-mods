"""Adversarial ownership boundary probes using existing isolated helpers."""
import sqlite3
import pytest
from test_goal_ownership import owner, gd, SID
from goal_consumer_fixture import consumer


@pytest.mark.parametrize('fingerprint', [None, 'UNVERIFIED', '12345', 'foreign-boot|12345'])
def test_unknown_or_foreign_process_identity_cannot_own(owner, consumer, fingerprint):
    with sqlite3.connect(owner[0]) as conn:
        conn.execute('UPDATE task_runs SET worker_started_at=?', (fingerprint,))
    with pytest.raises(ValueError, match='owner_live_continuation_unverified'):
        gd.validate_disposition(owner[1], owner[2], session_id=SID)


@pytest.mark.parametrize('platform', ['cli', 'telegram', 'slack', 'api'])
def test_non_tui_subscription_is_not_a_return_proof(owner, consumer, platform):
    with sqlite3.connect(owner[0]) as conn:
        conn.execute('UPDATE kanban_notify_subs SET platform=?', (platform,))
    with pytest.raises(ValueError, match='owner_result_return_unverified'):
        gd.validate_disposition(owner[1], owner[2], session_id=SID)


def test_process_reference_without_native_task_is_unknown(owner):
    owner[1]['continuation_ref'] = dict(pid=12345)
    with pytest.raises(ValueError, match='owner_readback_unavailable'):
        gd.validate_disposition(owner[1], owner[2], session_id=SID)


def test_historical_source_is_not_a_surface_certificate(owner, consumer):
    # Existing source labels are not checked. This documents scope, not support
    # for Slack transport: acceptance still relies on the native TUI consumer.
    with sqlite3.connect(owner[0].parent / 'state.db') as conn:
        conn.execute("UPDATE sessions SET source='slack'")
    assert gd.validate_disposition(owner[1], owner[2], session_id=SID)['disposition'] == 'owned_continuing'
