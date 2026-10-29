"""Ask delivery failures must be visible even while the rest of the batch works."""
from test_relay_retry import setup, plugin


def test_unsettled_failed_ask_is_watched(tmp_path, monkeypatch):
    led, _, tick = setup(tmp_path, monkeypatch)
    tick(1000)
    plugin._state['mono_seen'] = {}
    calls = []
    cards = {'ask': {'status': 'blocked', 'block_kind': 'needs_input', 'human_request': {
        'action': 'Choose', 'why_human': 'Only you know', 'interaction': 'reply',
        'scope': 'account', 'evidence': 'Two accounts'}}, 'work': {'status': 'running'}}
    args = dict(unseen={}, force=True, post=lambda *a: calls.append(a), read=lambda _: cards)
    plugin.watchdog_tick(now=1001, mono=1, **args)
    assert plugin.watchdog_tick(now=2902, mono=1902, **args)
    assert len(calls) == 1 and 'ask' in calls[0][1].lower()
    assert not plugin.watchdog_tick(now=4000, mono=3000, **args)


def test_unconfirmed_handoff_is_sleep_safe_and_files_once(tmp_path, monkeypatch):
    led, _, _ = setup(tmp_path, monkeypatch)
    plugin._state['mono_seen'] = {}
    plugin._state['post_failures'] = {}
    handoff = {'id': 'request', 'session_id': 'chat', 'created_at': 1000}
    monkeypatch.setattr(plugin.routing, 'stalled', lambda *a: [handoff])
    calls = []
    args = dict(unseen={}, force=True, post=lambda *a: calls.append(a), read=lambda _: {})
    assert not plugin.watchdog_tick(now=1000, mono=1, **args)
    assert not plugin.watchdog_tick(now=5000, mono=2, **args)  # wall time alone (sleep) never fires
    assert plugin.watchdog_tick(now=7000, mono=1902, **args) == ['chat-closeout:handoff:request']
    assert 'Inspect the target' in calls[0][1]
    assert not plugin.watchdog_tick(now=8000, mono=2902, **args)
    assert len(calls) == 1
