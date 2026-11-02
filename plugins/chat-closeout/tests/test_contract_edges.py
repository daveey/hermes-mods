"""Contract edges of the shepherd-settle release (review of 47726d45). Throwaway SQLite only.

1. Local id evidence: a 3-part legacy key whose id matches a local card never migrates; a missing 2/3/4-part key
   whose id matches ANY local card, or another inventory owner, is never unlinked.
2. Upgrade cutoff: only entries in the ledger at the first upgrade pass, not seen working and not finished after
   it, are covered silently. A card working at deploy, an entry added later, or a finish after the cutoff gets the
   normal close-out even while an old stale row keeps the upgrade incomplete; the upgrade completes only once every
   snapshot entry was observed (or left its batch).
3. Unlinked-only batch: an entry unlinked out of a batch that then holds nothing (or only covered cards) still gets
   ONE close-out naming it; it is forgotten only at the end of a successful close-out turn, not at the brief and not
   after a failed turn, and a failed delivery is retried a bounded number of times.
4. Replica completion times follow core's replica record keys (``profile/board/id``, legacy ``board/id``).
5. An uncertain entry (unreadable inventory) pauses its unlink clock: a short outage never counts.
6. Watchdog: a row whose only entries are remote done/archived rows that cannot settle (origin without binding,
   unfinished) and nothing found working is stuck; covered human asks are resting.
"""
import importlib
import json
import types
import os
import sqlite3
import time

import pytest
from test_closeout import plugin, core
from test_unlink import world, inv, local_board, H  # noqa: F401  (pytest fixture import)

DAY = 24 * H


def tick(now, mono=None, wake_result='started'):
    calls = []
    plugin.wake_tick(now=now, mono=now if mono is None else mono,
                     wake=lambda sid, prompt, **k: calls.append(prompt) or wake_result, unseen={},
                     last_user=lambda _: 0, read=plugin._pass_reader())
    return calls


@pytest.fixture
def hourly(monkeypatch):
    monkeypatch.setattr(core, 'UNLINK_MAX_GAP_S', 2 * H)


# ---- 1. local id evidence ----------------------------------------------------------------------------------------
def test_3part_legacy_with_local_id_is_not_migrated(world, tmp_path, hourly):
    led, inventory, boards = world
    boards.append(local_board(tmp_path / 'kanban.db', 't_one'))
    inventory.append(inv('hub/runners/default/t_one'))
    led.write('chat', batch=['hub/runners/t_one'], open=[])
    for h in range(30):
        tick(1000 + h * H)
    row = led.get('chat')
    assert row['batch'] == ['hub/runners/t_one']           # neither migrated nor unlinked
    assert 'hub/runners/t_one' not in row['unlinked']


@pytest.mark.parametrize('key', ['hub/default/build/t_one', 'hub/runners/t_one'])
def test_missing_key_with_local_id_is_never_unlinked(world, tmp_path, hourly, key):
    led, _, boards = world
    boards.append(local_board(tmp_path / 'kanban.db', 't_one'))
    led.write('chat', batch=[key], open=[])
    for h in range(30):
        tick(1000 + h * H)
    assert led.get('chat')['batch'] == [key]


def test_missing_board_key_with_same_id_on_another_board_is_never_unlinked(world, tmp_path, hourly):
    led, _, boards = world
    boards.append(local_board(tmp_path / 'boards' / 'other' / 'kanban.db', 't_one'))
    led.write('chat', batch=['default/t_one'], open=[])
    for h in range(30):
        tick(1000 + h * H)
    assert led.get('chat')['batch'] == ['default/t_one']


def test_missing_canonical_key_with_another_remote_owner_is_never_unlinked(world, hourly):
    led, inventory, _ = world
    inventory.append(inv('mini/default/bb/t_one'))
    led.write('chat', batch=['hub/default/build/t_one'], open=[])
    for h in range(30):
        tick(1000 + h * H)
    assert led.get('chat')['batch'] == ['hub/default/build/t_one']   # not renamed (4-part), not unlinked


def test_missing_canonical_key_with_no_id_match_is_unlinked(world, hourly):
    """Control: the guard above is id evidence, not a blanket refusal."""
    led, inventory, _ = world
    inventory.append(inv('hub/default/build/t_live'))
    led.write('chat', batch=['hub/default/build/t_gone', 'hub/default/build/t_live'], open=[])
    for h in range(26):
        tick(1000 + h * H)
    assert led.get('chat')['batch'] == ['hub/default/build/t_live']


# ---- 2. upgrade cutoff ---------------------------------------------------------------------------------------------
def srow(key, status='done', stale=False, unfinished=None, **kw):
    unfinished = status not in core.DONE_STATUSES if unfinished is None else unfinished
    gw, profile, board, tid = key.split('/')
    return {'id': tid, 'key': key, 'gateway': gw, 'profile': profile, 'board': board, 'remote': True,
            'status': status, 'stale': stale, 'unfinished': unfinished, 'via': 'shepherd', 'released': not unfinished,
            **kw}


OLD, WORK, STALE, LATER = ('hub/default/b/t_old', 'hub/default/b/t_work', 'hub/default/b/t_stale',
                           'hub/default/b/t_later')


@pytest.fixture
def upgrade(tmp_path, monkeypatch):
    led = core.Ledger(tmp_path / 'ledger.db')
    inventory = {OLD: srow(OLD), WORK: srow(WORK, 'running'), STALE: srow(STALE, stale=True)}
    led.write('chatA', batch=[OLD, WORK], open=[WORK])
    led.write('chatS', batch=[STALE], open=[])
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setattr(plugin, 'board_paths', lambda: [])
    monkeypatch.setattr(plugin, 'remote_cards', lambda: [dict(r) for r in inventory.values()])
    monkeypatch.setattr(plugin, 'hub_db_path', lambda: None)
    monkeypatch.setattr(plugin, '_hub_fresh', lambda: True)
    monkeypatch.setattr(plugin, '_lineage', lambda sid: [sid])
    monkeypatch.setattr(plugin, '_session_source', lambda sid: 'desktop')
    monkeypatch.setattr(plugin, 'session_proof', lambda *a: False)
    plugin._state.clear()
    return led, inventory


def utick(now):
    woke = []
    plugin.wake_tick(now=now, mono=now, wake=lambda sid, p, **k: woke.append(sid) or 'started', unseen={},
                     last_user=lambda _: 0, read=plugin._pass_reader())
    return woke


def test_card_working_at_deploy_closes_out_normally_while_upgrade_incomplete(upgrade):
    led, inventory = upgrade
    assert utick(10_000) == []
    assert led.get('chatA')['batch'] == [WORK]              # the old finish was covered silently
    assert led.meta_get(core.UPGRADE_SHEPHERD_KEY) is None  # STALE is not observed yet: incomplete
    inventory[WORK] = srow(WORK)
    assert utick(20_000) == ['chatA']                       # finished after deploy: its close-out runs


def test_entry_added_after_cutoff_closes_out_normally(upgrade):
    led, inventory = upgrade
    utick(10_000)
    led.write('chatB', batch=[LATER], open=[LATER])
    inventory[LATER] = srow(LATER)
    assert utick(20_000) == ['chatB']


def test_finish_after_cutoff_of_an_entry_first_seen_finished_closes_out(upgrade):
    """Stale at deploy, then seen finished with a completion time after the cutoff: not an old finish."""
    led, inventory = upgrade
    utick(10_000)
    cutoff = float(led.meta_get(core.UPGRADE_CUTOFF_KEY))
    inventory[STALE] = srow(STALE, completed_at=cutoff + 60)
    assert utick(20_000) == ['chatS']
    assert led.meta_get(core.UPGRADE_SHEPHERD_KEY) is not None  # every snapshot entry observed: complete


def test_old_stale_entry_seen_finished_before_cutoff_is_silent_and_completes(upgrade):
    led, inventory = upgrade
    utick(10_000)
    cutoff = float(led.meta_get(core.UPGRADE_CUTOFF_KEY))
    inventory[STALE] = srow(STALE, completed_at=cutoff - 3600)
    assert utick(20_000) == []
    assert led.get('chatS')['batch'] == []
    assert led.meta_get(core.UPGRADE_SHEPHERD_KEY) is not None


def test_upgrade_not_done_while_snapshot_entry_unobserved(upgrade):
    led, inventory = upgrade
    for n in range(5):
        utick(10_000 + n * 1000)
    assert led.meta_get(core.UPGRADE_SHEPHERD_KEY) is None
    led.write('chatS', batch=[])                             # it left its batch: nothing old remains
    utick(20_000)
    assert led.meta_get(core.UPGRADE_SHEPHERD_KEY) is not None


def test_stale_entry_added_after_cutoff_does_not_hold_the_upgrade(upgrade):
    led, inventory = upgrade
    utick(10_000)
    led.write('chatB', batch=['hub/default/b/t_new'], open=[])
    inventory['hub/default/b/t_new'] = srow('hub/default/b/t_new', stale=True)
    inventory[STALE] = srow(STALE, 'running')
    utick(20_000)
    assert led.meta_get(core.UPGRADE_SHEPHERD_KEY) is not None


def test_snapshot_survives_restart(upgrade):
    led, inventory = upgrade
    utick(10_000)
    plugin._state.clear()                                    # another process: snapshot and cutoff are durable
    inventory[WORK] = srow(WORK)
    assert utick(20_000) == ['chatA']


# ---- 3. unlinked-only batch: one close-out, acknowledged at a successful turn end ---------------------------------
OK_TURN = [{'role': 'user', 'content': 'x'},
           {'role': 'assistant', 'content': 'Closing answer.', 'finish_reason': 'stop'}]
FAILED_TURN = [{'role': 'user', 'content': 'x'},
               {'role': 'assistant', 'content': 'partial', 'finish_reason': 'length'}]


@pytest.fixture
def lone(world, hourly, monkeypatch):
    led, inventory, _ = world
    monkeypatch.setattr(plugin, 'open_card_ids', lambda sid: [])
    for name in ('HERMES_KANBAN_TASK', 'CHAT_CLOSEOUT_OFF'):
        monkeypatch.delenv(name, raising=False)
    clock = [0.0]                                            # the turn end's wall clock (core.time.time)
    monkeypatch.setattr(core, 'time', types.SimpleNamespace(time=lambda: clock[0]))
    led.write('chat', batch=['default/t_gone'], open=[])
    t = 1000.0
    for _ in range(25):
        tick(t, wake_result='busy')                          # busy: nothing delivered, no backoff
        t += H
    assert led.get('chat')['batch'] == []
    return led, t, clock


def turn_end(clock, at, history):
    clock[0] = at
    plugin.post_llm_call(session_id='chat', platform='cli', conversation_history=history)


def test_unlinked_only_batch_is_named_once(lone):
    led, t, clock = lone
    calls = tick(t)
    assert len(calls) == 1 and 'default/t_gone: no longer linked to this chat' in calls[0]
    turn_end(clock, t + 10, OK_TURN)
    assert core.unlinked_removed(led.get('chat')) == []
    assert all(tick(t + n * H) == [] for n in range(1, 5))
    assert led.get('chat') is not None and not led.pending()


def test_unlinked_only_row_is_pending_for_the_sidebar(lone):
    led, _, _ = lone
    assert 'chat' in core.pending_closeouts(led, plugin._pass_reader(light=True))


def test_unlinked_next_to_covered_human_ask_gets_its_closeout(world, hourly, tmp_path):
    led, inventory, _ = world
    ask = 'hub/default/build/t_ask'
    inventory.append({**inv(ask, 'blocked'), 'block_kind': 'needs_input'})
    led.write('chat', batch=['default/t_gone', ask], open=[], covered=[ask], done_sig='x')
    calls = []
    for h in range(26):
        calls += tick(1000 + h * H)
    assert len(calls) == 1 and 'default/t_gone: no longer linked' in calls[0]


def test_failed_closeout_turn_keeps_the_mention(lone):
    led, t, clock = lone
    assert len(tick(t)) == 1
    turn_end(clock, t + 10, FAILED_TURN)
    assert core.unlinked_removed(led.get('chat')) == ['default/t_gone']
    assert tick(t + 60) == []                                # shared backoff: no tight retry loop
    calls = tick(t + H)                                      # past the retry backoff
    assert len(calls) == 1 and 'default/t_gone: no longer linked' in calls[0]
    turn_end(clock, t + H + 10, OK_TURN)
    assert core.unlinked_removed(led.get('chat')) == []
    assert tick(t + 3 * H) == []


def test_brief_in_a_failed_user_turn_is_not_acknowledgement(lone):
    led, t, clock = lone
    out = plugin.pre_llm_call(session_id='chat', platform='cli')
    assert out and 'default/t_gone: no longer linked' in out['context']
    turn_end(clock, t, FAILED_TURN)
    assert core.unlinked_removed(led.get('chat')) == ['default/t_gone']
    calls = tick(t + H)
    assert len(calls) == 1 and 'default/t_gone: no longer linked' in calls[0]


def test_failed_deliveries_are_bounded(lone):
    led, t, clock = lone
    woken = 0
    for n in range(12):
        calls = tick(t + n * H)
        woken += len(calls)
        if calls:
            turn_end(clock, t + n * H + 10, FAILED_TURN)
    assert woken == core.UNLINK_MAX_TRIES
    assert core.unlinked_removed(led.get('chat')) == [] and not led.pending()


# ---- 4. replica completion times: core's record keys ---------------------------------------------------------------
def _replica(path, rows):
    """The live hub replica's schema (``hermes-hub``'s hub.db)."""
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE replica (gw TEXT, coll TEXT, key TEXT, version INTEGER, seq INTEGER, data TEXT, '
                   'updated_at INTEGER, PRIMARY KEY (gw, coll, key))')
        for n, (gw, key, data) in enumerate(rows):
            db.execute('INSERT INTO replica VALUES (?,?,?,?,?,?,?)',
                       (gw, 'tasks', key, 1, n + 1, json.dumps(data), int(time.time() * 1000)))
    return path


def test_replica_completion_uses_core_record_keys(tmp_path, monkeypatch):
    """Owner gateway ``macbook``: a qualified record ``default/<board>/<id>`` and a legacy ``<board>/<id>``
    (core projects it with ``profile`` None and key ``macbook/<board>/<id>``), as on the live hub replica."""
    hub = _replica(tmp_path / 'hub.db', [
        ('macbook', 'default/default/t_q', {'id': 't_q', 'status': 'done', 'completed_at': 500}),
        ('macbook', 'runners/t_l', {'id': 't_l', 'status': 'archived', 'completed_at': 400}),
        ('mini', 'default/default/t_q', {'id': 't_q', 'status': 'done', 'completed_at': 1}),  # other owner
    ])
    monkeypatch.setattr(plugin, 'hub_db_path', lambda: hub)
    monkeypatch.setattr(plugin, 'board_paths', lambda: [])
    q = {'id': 't_q', 'key': 'macbook/default/default/t_q', 'gateway': 'macbook', 'profile': 'default',
         'board': 'default', 'remote': True, 'status': 'done', 'stale': False, 'unfinished': False, 'via': 'shepherd'}
    legacy = {**q, 'id': 't_l', 'key': 'macbook/runners/t_l', 'profile': None, 'board': 'runners',
              'status': 'archived'}
    monkeypatch.setattr(plugin, 'remote_cards', lambda: [dict(q), dict(legacy)])
    cards = plugin.read_cards(['macbook/default/default/t_q', 'macbook/runners/t_l'])
    assert cards['macbook/default/default/t_q']['completed_at'] == 500
    assert cards['macbook/runners/t_l']['completed_at'] == 400


def test_replica_completion_through_real_core_projection(tmp_path, monkeypatch):
    source = os.environ.get('HERMES_CORE_SOURCE')
    if not source:
        pytest.skip('set HERMES_CORE_SOURCE for core projection integration')
    monkeypatch.syspath_prepend(source)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_HUB_STATE', str(tmp_path))
    ksa = importlib.import_module('hermes_cli.kanban_session_activity')
    profiles = importlib.import_module('hermes_cli.profiles')
    monkeypatch.setattr(profiles, 'get_active_profile_name', lambda: 'default')
    now = time.time()
    link = [{'session': 'chat', 'gw': 'hub', 'profile': 'default', 'rel': 'shepherd'}]
    hub = _replica(tmp_path / 'hub.db', [
        ('macbook', 'default/default/t_q', {'id': 't_q', 'status': 'done', 'completed_at': 500, 'sessions': link}),
        ('macbook', 'runners/t_l', {'id': 't_l', 'status': 'done', 'completed_at': 400, 'sessions': link}),
    ])
    with sqlite3.connect(hub) as db:
        db.execute('CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)')
        db.execute("INSERT INTO meta VALUES ('gateway', 'hub')")
        db.execute("INSERT INTO meta VALUES ('last_ok', ?)", (str(now),))
    rows = ksa._read_remote_links(hub, now=now)
    assert {(r['gateway'], r['profile'], r['board'], r['id']) for r in rows} == {
        ('macbook', 'default', 'default', 't_q'), ('macbook', None, 'runners', 't_l')}
    fill = [{**r, 'status': 'done'} for r in rows]           # finished rows (owner attestation is core's concern)
    monkeypatch.setattr(plugin, 'hub_db_path', lambda: hub)
    got = sorted(plugin._replica_completed_at(fill).values())
    assert got == [400.0, 500.0]


# ---- 5. uncertain entries pause the unlink clock -------------------------------------------------------------------
def test_short_unreadable_outage_is_not_observation(world, monkeypatch):
    led, inventory, _ = world
    led.write('chat', batch=['default/t_gone'], open=[])
    t = 1000.0
    for _ in range(11):                                      # 600 s observed
        tick(t)
        t += 60
    assert led.get('chat')['unlinked']['default/t_gone']['observed'] == 600
    good = plugin.remote_cards

    def broken():
        raise OSError('replica locked')
    monkeypatch.setattr(plugin, 'remote_cards', broken)
    for _ in range(4):                                       # a 4 min outage (under UNLINK_MAX_GAP_S)
        tick(t)
        t += 60
    monkeypatch.setattr(plugin, 'remote_cards', good)
    tick(t)
    assert led.get('chat')['unlinked']['default/t_gone']['observed'] == 600   # outage excluded
    tick(t + 60)
    assert led.get('chat')['unlinked']['default/t_gone']['observed'] == 660   # observation resumes


# ---- 6. watchdog: finished remote rows that can never settle ----------------------------------------------------------
def wd_run(cards, now, mono, posts):
    return plugin.watchdog_tick(now=now, mono=mono, unseen={}, force=True,
                                post=lambda title, body, key: posts.append((title, body, key)),
                                read=lambda ids: {k: v for k, v in cards.items() if k in ids})


@pytest.fixture
def wd(tmp_path, monkeypatch):
    led = core.Ledger(tmp_path / 'ledger.db')
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setattr(plugin, 'resolve_card', lambda key, res: True)
    plugin._state.clear()
    return led


K = 'hub/default/build/t_x'
UNSETTLED_DONE = [
    {'status': 'done', 'remote': True, 'stale': False, 'unfinished': False, 'via': 'origin'},           # no binding
    {'status': 'done', 'remote': True, 'stale': False, 'unfinished': True, 'via': 'origin',
     'binding_id': 'b-1'},
    {'status': 'archived', 'remote': True, 'stale': False, 'unfinished': True, 'via': 'shepherd'},
]


@pytest.mark.parametrize('card', UNSETTLED_DONE, ids=['origin-unbound', 'origin-unfinished', 'shepherd-unfinished'])
def test_finished_but_unsettled_remote_row_is_stuck(wd, card):
    posts = []
    wd.write('chat', batch=[K], open=[])
    wd_run({K: card}, 1000, 0, posts)
    assert wd_run({K: card}, 1000 + DAY, DAY, posts) == ['chat-closeout:stuck:chat:1000']
    assert K in posts[0][1]


def test_finished_unsettled_next_to_working_card_is_not_stuck(wd):
    posts = []
    cards = {K: UNSETTLED_DONE[0], 'default/t_run': {'status': 'running'}}
    wd.write('chat', batch=list(cards), open=[])
    for n in range(3):
        wd_run(cards, 1000 + n * DAY, n * DAY, posts)
    assert posts == []


@pytest.mark.parametrize('card', [
    {'status': 'blocked', 'block_kind': 'needs_input'},
    {'status': 'blocked', 'remote': True, 'stale': False, 'unfinished': True, 'via': 'shepherd',
     'block_kind': 'needs_input'},
], ids=['local-ask', 'shepherd-ask'])
def test_covered_human_ask_is_resting(wd, card):
    posts = []
    wd.write('chat', batch=[K], open=[], covered=[K])
    for n in range(3):
        wd_run({K: card}, 1000 + n * DAY, n * DAY, posts)
    assert posts == []


@pytest.mark.parametrize('history,ok', [
    (OK_TURN, True),
    (FAILED_TURN, False),
    ([{'role': 'assistant', 'content': 'x', 'finish_reason': 'stop', 'tool_calls': [{'id': '1'}]}], False),
    ([{'role': 'assistant', 'content': '  ', 'finish_reason': 'stop'}], False),
    ([{'role': 'assistant', 'content': [{'type': 'text', 'text': 'done'}], 'finish_reason': 'end_turn'}], True),
    ([{'role': 'assistant', 'content': 'x', 'finish_reason': 'stop', 'display_kind': 'commentary'}], False),
    ([{'role': 'user', 'content': 'x'}], False),
    (None, True),                                            # an older core passes no history: previous behaviour
], ids=['stop', 'length', 'tool-calls', 'blank', 'blocks-end-turn', 'commentary', 'no-reply', 'no-history'])
def test_turn_success_rule(history, ok):
    assert plugin._turn_succeeded(history) is ok
