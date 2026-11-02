"""One-time upgrade: ledger rows written before shepherd rows could settle hold shepherd cards that finished long ago.
The first pass after the upgrade covers them silently (one log line per row, no wake burst); later finishes close
out normally. Every waking path runs the upgrade first. Throwaway SQLite only."""
import logging

import pytest
from test_closeout import plugin, core

K1, K2, K3 = ('hub/default/build/t_a', 'hub/default/build/t_b', 'hub/default/build/t_c')


def row(key, status='done', stale=False, unfinished=None):
    unfinished = status not in core.DONE_STATUSES if unfinished is None else unfinished
    tid = key.rsplit('/', 1)[1]
    return {'id': tid, 'key': key, 'gateway': 'hub', 'profile': 'default', 'board': 'build', 'remote': True,
            'status': status, 'stale': stale, 'unfinished': unfinished, 'via': 'shepherd',
            'released': not unfinished}


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    """Rows as the previous release left them: shepherd cards finished, never settled."""
    led = core.Ledger(tmp_path / 'ledger.db')
    led.write('chatA', batch=[K1], open=[K1])
    led.write('chatB', batch=[K2, K3], open=[K2, K3])
    inventory = {K1: row(K1), K2: row(K2, 'archived'), K3: row(K3, 'running')}
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setattr(plugin, 'board_paths', lambda: [])
    monkeypatch.setattr(plugin, 'remote_cards', lambda: [dict(r) for r in inventory.values()])
    monkeypatch.setattr(plugin, 'hub_db_path', lambda: None)
    monkeypatch.setattr(plugin, '_lineage', lambda sid: [sid])
    monkeypatch.setattr(plugin, '_session_source', lambda sid: 'desktop')
    monkeypatch.setattr(plugin, 'session_proof', lambda *a: False)
    for name in ('HERMES_KANBAN_TASK', 'CHAT_CLOSEOUT_OFF'):
        monkeypatch.delenv(name, raising=False)
    plugin._state.clear()
    return led, inventory


def _tick(calls, now=10_000):
    return plugin.wake_tick(now=now, wake=lambda sid, p, **k: calls.append(sid) or 'started', unseen={},
                            last_user=lambda _: 0, read=plugin._pass_reader())


def _upgrade_lines(caplog):
    return [r.getMessage() for r in caplog.records if 'upgrade' in r.getMessage()]


def test_upgrade_covers_old_finishes_without_waking(legacy, caplog):
    led, _ = legacy
    calls = []
    with caplog.at_level(logging.INFO, logger='chat_closeout'):
        _tick(calls)
        _tick(calls, now=20_000)
    assert calls == []
    assert led.get('chatA')['batch'] == [] and led.get('chatA')['covered'] == []
    assert led.get('chatB')['batch'] == [K3]
    lines = _upgrade_lines(caplog)
    assert len(lines) == 2, lines                       # once per row, not per tick
    assert any('chatA' in l and K1 in l for l in lines) and any('chatB' in l and K2 in l for l in lines)


def test_later_finish_closes_out_normally(legacy):
    led, inventory = legacy
    calls = []
    _tick(calls)
    inventory[K3] = row(K3, 'done')
    _tick(calls, now=20_000)
    assert calls == ['chatB']
    core.record_turn_end(led, 'chatB', [], plugin.read_cards, now=20_010)
    assert led.get('chatB')['batch'] == []


@pytest.mark.parametrize('path', ['pre_llm_call', 'waiting_source', 'post_llm_call', 'watchdog_tick'])
def test_every_path_upgrades_before_it_acts(legacy, path, monkeypatch):
    led, _ = legacy
    if path == 'pre_llm_call':
        assert plugin.pre_llm_call(session_id='chatA', platform='cli') is None   # no close-out brief
    elif path == 'waiting_source':
        assert 'chatA' not in plugin.waiting_source()
    elif path == 'post_llm_call':
        monkeypatch.setattr(plugin, 'open_card_ids', lambda sid: [])
        plugin.post_llm_call(session_id='chatA', platform='cli')
    else:
        plugin.watchdog_tick(now=10_000, mono=10_000, unseen={}, post=lambda *a: None, force=True)
    assert led.get('chatA')['batch'] == []
    calls = []
    _tick(calls)
    assert calls == []


def test_stale_inventory_defers_upgrade_without_waking(legacy):
    led, inventory = legacy
    for key in list(inventory):
        inventory[key] = {**inventory[key], 'stale': True}
    calls = []
    _tick(calls)
    assert calls == [] and led.get('chatA')['batch'] == [K1]    # nothing proven, nothing changed
    for key in list(inventory):
        inventory[key] = {**inventory[key], 'stale': False}
    _tick(calls, now=20_000)
    assert calls == [] and led.get('chatA')['batch'] == []      # covered when first readable: still no burst


def test_unreadable_inventory_defers_upgrade(legacy, monkeypatch):
    led, inventory = legacy

    def unavailable():
        raise OSError('replica locked')
    monkeypatch.setattr(plugin, 'remote_cards', unavailable)
    calls = []
    _tick(calls)
    assert calls == [] and led.get('chatA')['batch'] == [K1]
    monkeypatch.setattr(plugin, 'remote_cards', lambda: [dict(r) for r in inventory.values()])
    _tick(calls, now=20_000)
    assert calls == [] and led.get('chatA')['batch'] == []


def test_upgrade_runs_once_across_restarts(legacy):
    led, inventory = legacy
    calls = []
    _tick(calls)
    plugin._state.clear()                               # a restart: the marker is durable, not in memory
    led.write('chatC', batch=['hub/default/build/t_d'], open=[])
    inventory['hub/default/build/t_d'] = row('hub/default/build/t_d')
    _tick(calls, now=20_000)
    assert calls == ['chatC']                           # a finish after the upgrade wakes as normal
