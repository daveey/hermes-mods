"""Legacy key canonicalization and unlinking of entries nothing can find any more.

- A bare ``id`` or 3-part ``gateway/profile/id`` with no local match that matches exactly one inventory owner key
  is rewritten to that key in the batch AND coverage. Two candidate owners, or a local card, leave it untouched.
- An entry matching nothing (no local card, no inventory row) is removed only after 24 h of observation while the
  inventory is fresh and readable. Outages are excluded (not counted) but do not reset the time already observed;
  wall time counts only as far as this process's monotonic clock confirms it. The next close-out names each
  removed entry once: "no longer linked to this chat".
Throwaway SQLite only."""
import sqlite3

import pytest
from test_closeout import plugin, core

H = 3600.0


def inv(key, status='running'):
    gw, profile, board, tid = key.split('/')
    done = status in core.DONE_STATUSES
    return {'id': tid, 'key': key, 'gateway': gw, 'profile': profile, 'board': board, 'remote': True,
            'status': status, 'stale': False, 'unfinished': not done, 'via': 'shepherd', 'released': done}


@pytest.fixture
def world(tmp_path, monkeypatch):
    led = core.Ledger(tmp_path / 'ledger.db')
    inventory: list = []
    boards: list = []
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setattr(plugin, 'board_paths', lambda: list(boards))
    monkeypatch.setattr(plugin, 'remote_cards', lambda: [dict(r) for r in inventory])
    monkeypatch.setattr(plugin, 'hub_db_path', lambda: None)
    monkeypatch.setattr(plugin, '_hub_fresh', lambda: True)
    monkeypatch.setattr(plugin, '_lineage', lambda sid: [sid])
    monkeypatch.setattr(plugin, '_session_source', lambda sid: 'desktop')
    monkeypatch.setattr(plugin, 'session_proof', lambda *a: False)
    plugin._state.clear()
    assert plugin.ensure_upgraded(lambda ids: {}) is True
    return led, inventory, boards


def tick(now, mono=None):
    calls = []
    plugin.wake_tick(now=now, mono=now if mono is None else mono,
                           wake=lambda sid, prompt, **k: calls.append(prompt) or 'started', unseen={},
                           last_user=lambda _: 0, read=plugin._pass_reader())
    return calls


def local_board(path, tid, status='running'):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS tasks (id TEXT, title TEXT, status TEXT)')
        db.execute('INSERT INTO tasks VALUES (?,?,?)', (tid, 'local', status))
    return path


# ---- canonicalization -----------------------------------------------------------------------------------------
@pytest.mark.parametrize('legacy,canonical', [
    ('t_one', 'hub/default/build/t_one'),
    ('hub/runners/t_one', 'hub/runners/default/t_one'),
])
@pytest.mark.parametrize('covered', [True, False])
def test_unique_legacy_key_is_canonicalized(world, legacy, canonical, covered):
    led, inventory, _ = world
    inventory.append(inv(canonical))
    led.write('chat', batch=[legacy], open=[legacy], covered=[legacy] if covered else [])
    tick(1000)
    row = led.get('chat')
    assert row['batch'] == [canonical]
    assert row['covered'] == []          # renamed, then uncovered: the canonical card is still running
    assert not row['unlinked']


def test_canonical_key_keeps_coverage_of_human_parked_card(world):
    led, inventory, _ = world
    canonical = 'hub/default/build/t_one'
    inventory.append({**inv(canonical, 'blocked'), 'block_kind': 'needs_input'})
    led.write('chat', batch=['t_one'], open=[], covered=['t_one'], done_sig='x')
    calls = tick(1000)
    row = led.get('chat')
    assert row['batch'] == [canonical] and row['covered'] == [canonical]
    assert calls == []                                   # same reported card: no new close-out


@pytest.mark.parametrize('legacy', ['t_one', 'hub/runners/t_one'])
def test_ambiguous_inventory_leaves_legacy_untouched(world, legacy):
    led, inventory, _ = world
    inventory.extend([inv('hub/runners/default/t_one'), inv('hub/default/build/t_one')])
    led.write('chat', batch=[legacy], open=[])
    for h in range(30):
        tick(1000 + h * H)
    assert led.get('chat')['batch'] == [legacy]          # neither renamed nor unlinked


def test_local_match_leaves_bare_key_untouched(world, tmp_path):
    led, inventory, boards = world
    boards.append(local_board(tmp_path / 'kanban.db', 't_one'))
    inventory.append(inv('hub/default/build/t_one'))
    led.write('chat', batch=['t_one'], open=[])
    for h in range(30):
        tick(1000 + h * H)
    assert led.get('chat')['batch'] == ['t_one']


# ---- unlinking ------------------------------------------------------------------------------------------------
@pytest.fixture
def gone(world, monkeypatch):
    led, inventory, _ = world
    monkeypatch.setattr(core, 'UNLINK_MAX_GAP_S', 2 * H)   # hourly ticks in the test; 30 s ticks in production
    inventory.append(inv('hub/default/build/t_live'))
    led.write('chat', batch=['default/t_gone', 'hub/default/build/t_live'], open=[])
    return led, inventory


def test_missing_entry_unlinked_after_24h_fresh_observation(gone):
    led, _ = gone
    for h in range(24):
        tick(1000 + h * H)
    assert 'default/t_gone' in led.get('chat')['batch']   # 23 h observed
    tick(1000 + 24 * H)
    row = led.get('chat')
    assert row['batch'] == ['hub/default/build/t_live']
    assert 'default/t_gone' in row['unlinked']


@pytest.mark.parametrize('outage', ['unreadable', 'stale'])
def test_outage_is_excluded_not_reset(gone, monkeypatch, outage):
    led, inventory = gone
    t = 1000.0
    for _ in range(13):                                   # 12 h observed
        tick(t)
        t += H
    good = plugin.remote_cards
    if outage == 'unreadable':
        def broken():
            raise OSError('replica locked')
        monkeypatch.setattr(plugin, 'remote_cards', broken)
    else:
        monkeypatch.setattr(plugin, 'remote_cards', lambda: [{**r, 'stale': True} for r in good()])
    for _ in range(30):                                   # 30 h outage: not counted
        tick(t)
        t += H
    assert 'default/t_gone' in led.get('chat')['batch']
    monkeypatch.setattr(plugin, 'remote_cards', good)
    for _ in range(12):                                   # resumes at 12 h, not 0: first tick adds nothing
        tick(t)
        t += H
    assert 'default/t_gone' in led.get('chat')['batch']   # 23 h
    tick(t)
    assert 'default/t_gone' not in led.get('chat')['batch']


def test_stale_hub_meta_is_an_outage(gone, monkeypatch):
    led, _ = gone
    monkeypatch.setattr(plugin, '_hub_fresh', lambda: False)
    for h in range(40):
        tick(1000 + h * H)
    assert 'default/t_gone' in led.get('chat')['batch']


def test_wall_jump_without_monotonic_time_is_not_observation(gone):
    """A lid sleep: wall time moves 30 h, the monotonic clock barely moves."""
    led, _ = gone
    tick(1000, mono=50)
    tick(1000 + 30 * H, mono=110)
    assert led.get('chat')['unlinked']['default/t_gone']['observed'] <= 60
    assert 'default/t_gone' in led.get('chat')['batch']


def test_restart_does_not_count_unobserved_time(gone, monkeypatch):
    led, _ = gone
    tick(1000, mono=10)
    monkeypatch.setattr(core.os, 'getpid', lambda: 999_999)   # another process, its own monotonic clock
    tick(1000 + 20 * H, mono=5)
    assert led.get('chat')['unlinked']['default/t_gone']['observed'] == 0
    assert led.get('chat')['unlinked']['default/t_gone']['since'] == 1000   # first missing time persisted


def test_entry_found_again_forgets_its_missing_time(gone):
    led, inventory = gone
    back = 'hub/default/build/t_back'
    led.write('chat', batch=['default/t_gone', 'hub/default/build/t_live', back])
    for h in range(20):
        tick(1000 + h * H)
    assert led.get('chat')['unlinked'][back]['observed'] >= 19 * H
    inventory.append(inv(back))
    tick(1000 + 20 * H)
    assert back not in led.get('chat')['unlinked']
    inventory.pop()
    tick(1000 + 21 * H)
    assert led.get('chat')['unlinked'][back]['observed'] == 0     # a new absence starts its own clock
    assert led.get('chat')['unlinked'][back]['since'] == 1000 + 21 * H


def test_closeout_names_each_unlinked_entry_once(gone):
    led, inventory = gone
    for h in range(25):
        tick(1000 + h * H)
    assert 'default/t_gone' not in led.get('chat')['batch']
    inventory[0] = inv('hub/default/build/t_live', 'done')
    calls = tick(1000 + 26 * H)
    assert len(calls) == 1
    assert 'default/t_gone: no longer linked to this chat' in calls[0]
    inventory.append(inv('hub/default/build/t_next'))
    core.record_turn_end(led, 'chat', ['hub/default/build/t_next'], plugin.read_cards, now=1000 + 26 * H + 10)
    assert led.get('chat')['batch'] == ['hub/default/build/t_next']
    inventory[-1] = inv('hub/default/build/t_next', 'done')
    calls = tick(1000 + 28 * H)
    assert len(calls) == 1 and 'no longer linked' not in calls[0]


def test_failed_wake_keeps_unlinked_for_the_next_closeout(gone):
    led, inventory = gone
    for h in range(25):
        tick(1000 + h * H)
    inventory[0] = inv('hub/default/build/t_live', 'done')
    plugin.wake_tick(now=1000 + 26 * H, mono=1000 + 26 * H, wake=lambda *a, **k: 'error', unseen={},
                     last_user=lambda _: 0, read=plugin._pass_reader())
    calls = tick(1000 + 30 * H)
    assert len(calls) == 1 and 'default/t_gone: no longer linked to this chat' in calls[0]
