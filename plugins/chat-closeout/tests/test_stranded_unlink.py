"""Stranded entries: found remote rows that nothing can ever move on age out of the batch like missing ones.

- A fresh remote done/archived row without the owner evidence that settles it (origin without binding) stays
  unsettled (never a delivered close-out) but leaves the batch after 24 h of healthy observation; the stuck
  watchdog incident for its chat clears.
- A linked shepherd row the fresh replica stopped publishing (core keeps the last copy, marked stale, forever; e.g.
  an old card archived from todo without completion) ages the same way. A failed replica read (EVERY row stale) is
  an outage: not observation.
- Such ghost rows elsewhere in the inventory no longer make every pass unhealthy, so missing entries still age.
- A four-part key whose only other inventory owner is its own unqualified legacy row (gateway/board/id) resolves
  from the board's fresh full shadow; without a shadow row the alias still vetoes absence.
Throwaway SQLite only."""
import sqlite3

import pytest
from test_closeout import plugin, core
from test_unlink import world, inv, tick, H  # noqa: F401  (pytest fixture import)

DAY = 24 * H
LIVE = 'hub/default/build/t_live'


@pytest.fixture
def hourly(world, monkeypatch):
    monkeypatch.setattr(core, 'UNLINK_MAX_GAP_S', 2 * H)   # hourly ticks in the test; 30 s ticks in production
    led, inventory, boards = world
    inventory.append(inv(LIVE))
    return led, inventory


def archived_unbound(key):
    gw, profile, board, tid = key.split('/')
    return {'id': tid, 'key': key, 'gateway': gw, 'profile': profile, 'board': board, 'remote': True,
            'status': 'archived', 'stale': False, 'unfinished': False, 'via': 'origin', 'binding_id': None,
            'released': True}


def ghost(key, status='todo'):
    return {**inv(key, status), 'stale': True}


def run_hours(n, start=1000.0):
    for h in range(n):
        tick(start + h * H)


# ---- predicates ------------------------------------------------------------------------------------------------
def test_stranded_predicate():
    k = 'hub/default/default/t_x'
    assert core.is_stranded(archived_unbound(k))
    assert not core.is_settled(archived_unbound(k))                       # never a delivered close-out
    assert core.is_stranded(ghost(k)) and core.is_stranded(ghost(k, 'scheduled'))
    assert not core.is_stranded({**ghost(k), 'via': 'origin'})            # origin staleness: not this path
    assert not core.is_stranded(inv(k))                                   # fresh, working
    assert not core.is_stranded({**archived_unbound(k), 'binding_id': 'b'})   # settles: not stranded
    assert not core.is_stranded({'status': 'archived'})                   # local card
    assert not core.is_stranded(None)


# ---- the t_7e2ee45c shape: fresh archived origin row without binding --------------------------------------------
def test_fresh_archived_origin_row_without_binding_unlinks_after_24h(hourly, monkeypatch):
    led, inventory = hourly
    K = 'hub/default/default/t_arch'
    inventory.append(archived_unbound(K))
    led.write('chat', batch=[K, LIVE], open=[])
    run_hours(24)
    row = led.get('chat')
    assert K in row['batch'] and row['unlinked'][K]['found'] is True    # 23 h observed
    assert not row.get('done_sig')
    tick(1000 + 24 * H)
    row = led.get('chat')
    assert row['batch'] == [LIVE]
    assert row['unlinked'][K]['removed'] and row['unlinked'][K]['found'] is True


def test_stuck_incident_clears_once_the_stranded_entry_unlinks(hourly, monkeypatch):
    led, inventory = hourly
    K = 'hub/default/default/t_arch'
    inventory.append(archived_unbound(K))
    led.write('chat', batch=[K], open=[])
    resolved, posts = [], []
    monkeypatch.setattr(plugin, 'resolve_card', lambda key, res: resolved.append((key, res)) or True)

    def watch(now, mono):
        return plugin.watchdog_tick(now=now, mono=mono, unseen={}, force=True,
                                    post=lambda title, body, key: posts.append(key))
    watch(1000, 0)
    tick(1000)
    assert watch(1000 + DAY - 1, DAY - 1) == []
    run_hours(24, start=1000 + H)                                        # 24 h of healthy observation
    row = led.get('chat')
    assert K not in row['batch'] and core.unlinked_removed(row) == [K]
    # Filed before the unlink pass? Either way the incident is not observed any more and resolves.
    key = 'chat-closeout:stuck:chat:1000'
    led.flag_once(key, 1000 + DAY)
    assert watch(1000 + DAY + 60, DAY + 60) == []
    assert [k for k, _ in resolved] == [key] and not led.has_flag(key)


def test_closeout_names_a_stranded_entry_as_not_delivered(hourly):
    led, inventory = hourly
    K = 'hub/default/default/t_arch'
    inventory.append(archived_unbound(K))
    led.write('chat', batch=[K], open=[])
    calls = []
    for h in range(26):
        calls += tick(1000 + h * H)
    assert len(calls) == 1
    assert K in calls[0] and 'Not a delivered result' in calls[0]


# ---- the runners shape: stale ghost shepherd rows, live replica ------------------------------------------------
@pytest.mark.parametrize('status', ['todo', 'scheduled', 'unknown'])
def test_stale_ghost_row_unlinks_after_24h_of_healthy_reads(hourly, status):
    led, inventory = hourly
    G = 'hub/default/runners/t_ghost'
    inventory.append(ghost(G, status))
    led.write('chat', batch=[G, LIVE], open=[])
    run_hours(24)
    assert G in led.get('chat')['batch']
    tick(1000 + 24 * H)
    assert led.get('chat')['batch'] == [LIVE]


def test_failed_read_all_rows_stale_is_an_outage_not_observation(hourly, monkeypatch):
    led, inventory = hourly
    G = 'hub/default/runners/t_ghost'
    inventory.append(ghost(G))
    led.write('chat', batch=[G, LIVE], open=[])
    good = plugin.remote_cards
    monkeypatch.setattr(plugin, 'remote_cards', lambda: [{**r, 'stale': True} for r in good()])
    run_hours(40)
    assert G in led.get('chat')['batch']
    assert led.get('chat')['unlinked'].get(G, {}).get('observed', 0) == 0


def test_stranded_entry_found_working_again_forgets_its_clock(hourly):
    led, inventory = hourly
    G = 'hub/default/runners/t_ghost'
    inventory.append(ghost(G))
    led.write('chat', batch=[G, LIVE], open=[])
    run_hours(20)
    assert led.get('chat')['unlinked'][G]['observed'] >= 19 * H
    inventory[-1] = inv(G)                                               # published again, working
    tick(1000 + 20 * H)
    assert G not in led.get('chat')['unlinked'] and G in led.get('chat')['batch']


def test_ghost_rows_elsewhere_do_not_stop_missing_entries_aging(hourly):
    led, inventory = hourly
    inventory.append(ghost('hub/default/runners/t_other'))               # another chat's ghost
    led.write('chat', batch=['default/t_gone', LIVE], open=[])
    run_hours(25)
    assert led.get('chat')['batch'] == [LIVE]


# ---- legacy unqualified alias + fresh full-board shadow ---------------------------------------------------------
def _shadow_row(root, board, tid, status, created_on='hub'):
    path = root / 'kanban' / 'sync-shadow' / f'{board}.db'
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE IF EXISTS tasks')
        db.execute('CREATE TABLE tasks (id TEXT, status TEXT, created_on TEXT)')
        db.execute('INSERT INTO tasks VALUES (?,?,?)', (tid, status, created_on))


def _alias(tid, board='build'):
    return {**inv(f'hub/default/{board}/{tid}'), 'key': f'hub/{board}/{tid}', 'profile': None,
            'status': 'unknown', 'ownership_state': 'unknown'}


def test_legacy_alias_owner_lets_the_fresh_shadow_settle_the_qualified_key(world):
    led, inventory, _ = world
    K = 'hub/default/build/t_arch'
    _shadow_row(plugin._kanban_root(), 'build', 't_arch', 'archived')
    inventory.append(_alias('t_arch'))
    cards = plugin.read_cards([K], remote_inventory=lambda: [dict(r) for r in inventory])
    assert K not in cards.uncertain
    assert cards[K]['via'] == 'shadow' and core.is_settled(cards[K])


def test_legacy_alias_without_shadow_row_still_vetoes_absence(world, monkeypatch):
    led, inventory, _ = world
    monkeypatch.setattr(core, 'UNLINK_MAX_GAP_S', 2 * H)
    K = 'hub/default/build/t_arch'
    inventory.append(_alias('t_arch'))
    cards = plugin.read_cards([K], remote_inventory=lambda: [dict(r) for r in inventory])
    assert K not in cards and K in cards.uncertain and K in cards.absence_veto
    led.write('chat', batch=[K], open=[])
    run_hours(30)
    assert led.get('chat')['batch'] == [K]


def test_another_owner_still_blocks_shadow_resolution(world):
    led, inventory, _ = world
    K = 'hub/default/build/t_arch'
    _shadow_row(plugin._kanban_root(), 'build', 't_arch', 'archived')
    inventory.append(inv('mini/default/build/t_arch'))                   # a different gateway owns that id
    cards = plugin.read_cards([K], remote_inventory=lambda: [dict(r) for r in inventory])
    assert K not in cards and K in cards.uncertain
