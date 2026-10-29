"""Real courier file protocol, durable one-shot intents and receipt handling."""
import json
import pytest
from test_relay_retry import plugin, core

routing = plugin.routing


@pytest.fixture
def env(tmp_path, monkeypatch):
    rf = routing.courier()
    base = tmp_path / 'courier'
    for sub in ('outbox', 'claimed', 'results'):
        (base / sub).mkdir(parents=True)
    monkeypatch.setattr(rf, 'root', lambda: base)
    return core.Ledger(tmp_path / 'ledger.db'), rf, base


def test_real_courier_one_request_across_restart_and_named_profile(env):
    led, rf, base = env
    assert routing.handoff(led, 'chat', 'remote', 'ask:key', 'Choose the account', 1000, 'work') == 'forwarded'
    paths = list((base / 'outbox').glob('*.json'))
    assert len(paths) == 1
    req = json.loads(paths[0].read_text())
    assert req['route'] == 'remote' and req['args']['target'] == '@session:work@remote/chat'
    assert req['args']['message'] == 'Choose the account'
    assert rf.claim_pending(base, courier='desktop', reach='remote') == [req]
    routing.handoff(core.Ledger(led.path), 'chat', 'remote', 'ask:key', 'Choose the account', 2000, 'work')
    assert list((base / 'outbox').glob('*.json')) == []
    assert len(list((base / 'claimed').glob('*.json'))) == 1
    assert len(routing.stalled(led, 3000, 1800)) == 1
    rf.atomic_write(base / 'results' / paths[0].name, {'ok': True, 'status': 'streaming', 'delivered': True})
    assert routing.stalled(led, 3001, 1800) == []
    # Receipt persists even after the transport removes its result.
    (base / 'results' / paths[0].name).unlink()
    assert routing.stalled(core.Ledger(led.path), 4000, 1800) == []


def test_crash_or_unknown_send_never_reenqueues(env, monkeypatch):
    led, rf, base = env
    calls = []
    def crash(*args):
        calls.append(args)
        raise OSError('uncertain write')
    monkeypatch.setattr(rf, 'enqueue', crash)
    with pytest.raises(OSError):
        routing.handoff(led, 'chat', 'remote', 'ask:key', 'Choose', 1000)
    assert routing.handoff(core.Ledger(led.path), 'chat', 'remote', 'ask:key', 'Choose', 2000) == 'forwarded'
    assert len(calls) == 1
    assert routing.stalled(led, 3000, 1800)


def test_failed_receipt_not_delivery(env):
    led, rf, base = env
    routing.handoff(led, 'chat', 'remote', 'ask:key', 'Choose', 1000)
    path = next((base / 'outbox').glob('*.json'))
    rf.atomic_write(base / 'results' / path.name, {'ok': False, 'status': 'unknown', 'delivered': None})
    assert routing.stalled(led, 3000, 1800)
