"""Sidebar waiting source cost: one remote-inventory read per pass, one pass per concurrent burst of polls."""
import threading
import time

import pytest
from test_closeout import plugin, core


def remote_row(n, status='done'):
    return {'id': f't_{n}', 'key': f'owner/work/build/t_{n}', 'gateway': 'owner', 'profile': 'work',
            'board': 'build', 'remote': True, 'status': status, 'unfinished': status != 'done',
            'stale': False, 'via': 'origin', 'binding_id': f'binding-{n}'}


@pytest.fixture
def pending(tmp_path, monkeypatch):
    """Three chats, each with one settled remote card and no close-out yet; counts remote_cards() calls."""
    led = core.Ledger(tmp_path / 'ledger.db')
    rows = [remote_row(n) for n in range(3)]
    for n, row in enumerate(rows):
        led.write(f'chat{n}', batch=[row['key']], open=[])
    calls = {'n': 0}

    def remote_cards():
        calls['n'] += 1
        time.sleep(0.05)  # long enough that concurrent polls overlap
        return [dict(r) for r in rows]

    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setattr(plugin, 'board_paths', lambda: [])
    monkeypatch.setattr(plugin, 'remote_cards', remote_cards)
    monkeypatch.setattr(plugin, 'session_proof', lambda *a: False)
    monkeypatch.delenv('CHAT_CLOSEOUT_OFF', raising=False)
    monkeypatch.delitem(plugin._state, 'waiting_cache', raising=False)
    expected = {f'chat{n}': [row['key']] for n, row in enumerate(rows)}
    return calls, expected


def test_one_remote_read_per_pass(pending):
    calls, expected = pending
    assert plugin.waiting_source() == expected
    assert calls['n'] == 1


def test_unavailable_inventory_read_once_and_not_waiting(pending, monkeypatch):
    calls, _ = pending

    def unavailable():
        calls['n'] += 1
        raise OSError('replica locked')

    monkeypatch.setattr(plugin, 'remote_cards', unavailable)
    # Bare remote ownership can't be proven without the inventory: nothing is reported settled.
    assert plugin.waiting_source() == {}
    assert calls['n'] == 1


def test_concurrent_polls_share_one_pass(pending):
    calls, expected = pending
    results = []
    threads = [threading.Thread(target=lambda: results.append(plugin.waiting_source())) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [expected] * 8
    assert calls['n'] == 1


def test_waker_tick_reads_remote_inventory_once(pending):
    calls, _ = pending
    woken = []
    plugin.wake_tick(now=10_000, wake=lambda sid, *a, **k: woken.append(sid) or 'busy', unseen={},
                     last_user=lambda _: 0, proof=lambda *a: False)
    assert len(woken) == 3  # every settled chat was considered for its close-out
    assert calls['n'] == 1


def test_watchdog_tick_reads_remote_inventory_once(pending, tmp_path):
    calls, expected = pending
    led = plugin.ledger()
    for sid in expected:
        led.write(sid, settled_sig=f'sig-{sid}', settled_at=1)
    plugin.watchdog_tick(now=10_000, mono=10_000, unseen={}, post=lambda *a: None, force=True)
    assert calls['n'] == 1
