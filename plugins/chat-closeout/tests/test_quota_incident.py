"""Quota waits hold incidents across processes; absence of observation is not clearance."""
import pytest
from test_relay_retry import plugin, setup


def settled(tmp_path, monkeypatch):
    led, _, _ = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(plugin, '_state', {})
    monkeypatch.setattr(plugin, '_session_gone', lambda _: False)
    cards = {'work': {'status': 'done'}}
    sig = plugin.core.closeout_key({'batch': ['work']}, cards)
    led.write('chat', batch=['work'], settled_sig=sig, settled_at=1)
    posts, resolutions = [], []
    monkeypatch.setattr(plugin, 'resolve_card', lambda key, evidence: resolutions.append(evidence) or True)
    args = dict(unseen={}, force=True, read=lambda _: cards,
                post=lambda *args: posts.append(args) or 'incident')
    return led, sig, posts, resolutions, args


def test_three_hour_two_process_quota_error_flapping(tmp_path, monkeypatch):
    led, sig, posts, resolutions, args = settled(tmp_path, monkeypatch)
    states = [{}, {}]
    # Both processes age the same incident, sharing only the durable ledger.
    for state in states:
        monkeypatch.setattr(plugin, '_state', state)
        plugin.watchdog_tick(now=2, mono=2, **args)
    plugin.watchdog_tick(now=1903, mono=1903, **args)
    probes = []
    for n, now in enumerate(range(2000, 14001, 300)):
        monkeypatch.setattr(plugin, '_state', states[n % 2])
        led.write('chat', wake_result='quota' if n % 3 else 'error',
                  retry_after=now - 1 if n % 4 < 2 else now + 3600)
        def pool(sid, at=None, n=n):
            probes.append(n % 2)
            return None if n % 2 else now + 3600
        monkeypatch.setattr(plugin, '_quota_wall', pool)
        plugin.watchdog_tick(now=now, mono=now, **args)
    assert set(probes) == {0, 1}  # exercise both disagreeing processes after the persisted clock expires
    assert len(posts) == 1
    assert resolutions == []
    assert led.has_flag(f'chat-closeout:no-closeout:chat:{sig}')


def test_persisted_quota_clock_never_probes_pool(tmp_path, monkeypatch):
    led, _, posts, _, args = settled(tmp_path, monkeypatch)
    led.write('chat', wake_result='quota', retry_after=10000)
    def forbidden(*args):
        pytest.fail('persisted quota clock must not re-probe the pool')
    monkeypatch.setattr(plugin, '_quota_wall', forbidden)
    plugin.watchdog_tick(now=2, mono=2, **args)
    plugin.watchdog_tick(now=1903, mono=1903, **args)
    assert posts == []


@pytest.mark.parametrize('empty_batch', [False, True])
def test_same_sig_with_changed_projection_is_not_positive_clearance(tmp_path, monkeypatch, empty_batch):
    led, sig, _, resolutions, args = settled(tmp_path, monkeypatch)
    plugin.watchdog_tick(now=2, mono=2, **args)
    plugin.watchdog_tick(now=1903, mono=1903, **args)
    led.write('chat', covered=['work'], **({'batch': []} if empty_batch else {}))
    plugin.watchdog_tick(now=2000, mono=2000, **args)
    assert resolutions == []
    assert not plugin._cleared_evidence(f'chat-closeout:no-closeout:chat:{sig}', {}, led)


@pytest.mark.parametrize('change,reason', [('done', 'done_sig matched'), ('moved', 'batch moved on'),
                                         ('gone', 'deleted'), ('row-gone', 'ledger row absent')])
def test_positive_evidence_resolves_even_during_quota(tmp_path, monkeypatch, change, reason):
    led, sig, _, resolutions, args = settled(tmp_path, monkeypatch)
    plugin.watchdog_tick(now=2, mono=2, **args)
    plugin.watchdog_tick(now=1903, mono=1903, **args)
    led.write('chat', wake_result='quota', retry_after=10000)
    if change == 'done':
        led.write('chat', done_sig=sig)
    elif change == 'moved':
        led.write('chat', settled_sig='new-batch')
    elif change == 'gone':
        monkeypatch.setattr(plugin, '_session_gone', lambda _: True)
    else:
        monkeypatch.setattr(led, 'get', lambda _: None)
        monkeypatch.setattr(led, 'pending', lambda: [])
    plugin.watchdog_tick(now=2000, mono=2000, **args)
    assert len(resolutions) == 1
    assert reason in resolutions[0]['evidence']


def test_ask_quota_wait_holds_existing_flag_and_monotonic_age(tmp_path, monkeypatch):
    from test_quota_wall import ASK
    led, _, tick = setup(tmp_path, monkeypatch, 'error')
    monkeypatch.setattr(plugin, '_state', {})
    monkeypatch.setattr(plugin, '_quota_wall', lambda *args: None)
    tick(1000)
    posts, resolutions = [], []
    monkeypatch.setattr(plugin, 'resolve_card', lambda *args: resolutions.append(args) or True)
    args = dict(unseen={}, force=True, read=lambda _: ASK, post=lambda *args: posts.append(args))
    plugin.watchdog_tick(now=1001, mono=1, **args)
    key, = plugin.watchdog_tick(now=2902, mono=1902, **args)
    first = plugin._state['mono_seen'][key]
    led.write('chat', wake_result='quota', retry_after=10000)
    monkeypatch.setattr(plugin, '_quota_wall', lambda *args: 10000)
    plugin.watchdog_tick(now=3000, mono=2000, **args)
    assert resolutions == [] and led.has_flag(key)
    assert plugin._state['mono_seen'][key] == first
