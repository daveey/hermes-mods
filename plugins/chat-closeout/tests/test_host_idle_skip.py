"""The host wake pass on an idle gateway (card t_01a1deb500fca100; measured: 45 of 45 ticks recomputed ~11 rows).

A row that is not quiet but whose inputs are unchanged is not read or judged again until its own deadline; a row
whose only activity is its unlink observation clock keeps that durable write and nothing else. Real core, real
state.db, ledger and board files (the ``world`` fixture of test_waiting_inputs).
"""
import sqlite3
import time as real_time

import pytest
from test_closeout import plugin, core
from test_waiting_inputs import world  # noqa: F401  (fixture)

la = pytest.importorskip('hermes_cli.loop_accounting')

T0 = 1_000_000.0


class Clock:
    """Stands in for the plugin's ``time`` module: ``time()``/``monotonic()`` move only when told to."""

    def __init__(self, wall=T0):
        self.wall = wall

    def time(self):
        return self.wall

    def monotonic(self):
        return self.wall - T0 + 5000.0

    def __getattr__(self, name):
        return getattr(real_time, name)


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(plugin, 'time', c)
    return c


@pytest.fixture
def spy(world, monkeypatch):  # noqa: F811
    """Counts ledger writes and last-user reads; the fixture already counts card reads and judgments."""
    seen = {'writes': [], 'last_user': 0, 'woken': []}
    real_write = world['led'].write

    def write(sid, **kw):
        seen['writes'].append((sid, sorted(kw)))
        return real_write(sid, **kw)

    monkeypatch.setattr(world['led'], 'write', write)
    monkeypatch.setattr(plugin, '_unseen_counts', lambda: {})
    monkeypatch.setattr(plugin, '_hub_fresh', lambda: True)
    monkeypatch.setitem(plugin._state, 'relinked', True)  # no live board read (relink_open_cards)
    seen['reset'] = lambda: (seen['writes'].clear(), seen['woken'].clear(), seen.update(last_user=0),
                             world['work'].update(reconcile_done=[], read_cards=0))
    return seen


def tick(spy, last_user=0):
    def user(_ids):
        spy['last_user'] += 1
        return last_user() if callable(last_user) else last_user

    def wake(sid, *a, **k):
        spy['woken'].append(sid)
        return 'busy'

    return plugin.wake_tick(wake=wake, last_user=user)


def counters(name='chat-closeout.wake_tick'):
    return dict(la.counters().get(name) or {'runs': 0, 'skips': 0, 'changes': 0, 'rows': 0})


def settle_world(world):
    """c1 and c2 hold settled cards (a close-out is due unless the user is typing); c3 works (quiet)."""
    return world


# (a) non-quiet rows, unchanged inputs: nothing is read, written or judged, and the accounting counts a skip ------
def test_a_not_due_row_with_unchanged_inputs_is_not_judged_again(world, spy, clock):  # noqa: F811
    typing = lambda: clock.wall - 10  # noqa: E731  the user typed 10 s ago in every chat: no close-out is due
    tick(spy, typing)  # records the settled signatures
    tick(spy, typing)  # judges them again: nothing to write now
    spy['reset']()
    before = counters()
    clock.wall += 30
    assert tick(spy, typing) == []
    assert world['work'] == {'reconcile_done': [], 'read_cards': 0}  # no board read, no judgment
    assert spy['writes'] == [] and spy['last_user'] == 0 and spy['woken'] == []  # no write, no state.db query
    after = counters()
    assert after['runs'] - before['runs'] == 1 and after['skips'] - before['skips'] == 1
    assert after.get('changes', 0) == before.get('changes', 0)


# (b) a change to a row, its card or its lineage transcript shows on the next tick -----------------------------------
@pytest.fixture
def idle(world, spy, clock):  # noqa: F811
    typing = lambda: clock.wall - 10  # noqa: E731
    tick(spy, typing)
    tick(spy, typing)
    spy['reset']()
    clock.wall += 30
    tick(spy, typing)
    assert world['work'] == {'reconcile_done': [], 'read_cards': 0}  # idle: the premise of the tests below
    spy['reset']()
    return typing


def test_b_a_card_change_is_judged_on_the_next_tick(world, spy, clock, idle):  # noqa: F811
    with sqlite3.connect(world['board']) as conn:
        conn.execute("UPDATE tasks SET status = 'running', completed_at = NULL WHERE id = 't_2'")
    clock.wall += 30
    tick(spy, idle)
    assert world['work']['read_cards'] >= 1 and 'c2' in world['work']['reconcile_done']


def test_b_a_row_change_is_judged_on_the_next_tick(world, spy, clock, idle):  # noqa: F811
    world['led'].write('c1', failure_since=5.0, retry_after=1.0)
    spy['reset']()
    clock.wall += 30
    tick(spy, idle)
    assert 'c1' in world['work']['reconcile_done']


def test_b_a_lineage_transcript_change_is_judged_on_the_next_tick(world, spy, clock, idle):  # noqa: F811
    with sqlite3.connect(world['state']) as conn:
        conn.execute("INSERT INTO messages (session_id, role, content, timestamp, active) "
                     "VALUES ('c1', 'user', 'hello', 200, 1)")
    clock.wall += 30
    tick(spy, idle)
    assert world['work']['reconcile_done'] == ['c1']  # only the chat whose transcript moved


# (c) a row's deadline arriving re-judges that row, with today's outcome -------------------------------------------
@pytest.mark.parametrize('memo', [True, False], ids=['memoized', 'every-tick-full'])
def test_c_the_quiet_period_ending_wakes_the_chat_as_before(world, spy, clock, memo):  # noqa: F811
    typed_at = clock.wall - 100  # quiet until typed_at + 300 = T0 + 200
    outcomes = []
    for step in range(8):
        if not memo:
            plugin._state.pop('host_memo', None)
            plugin._state.pop('host_clock', None)
            plugin._wi_get('host_rows', plugin.wi.RowCache).clear()
        tick(spy, typed_at)
        outcomes.append((clock.wall - T0, tuple(spy['woken'])))
        clock.wall += 30
    first_wake = next(at for at, woken in outcomes if woken)
    assert first_wake == 210.0  # the first tick after the deadline (T0 + 200), same with or without the memo
    assert sorted(set(outcomes[-1][1])) == ['c1', 'c2']


def test_c_the_memo_skips_until_the_deadline_and_not_after(world, spy, clock):  # noqa: F811
    typed_at = clock.wall - 100
    tick(spy, typed_at)
    tick(spy, typed_at)
    spy['reset']()
    clock.wall += 30
    tick(spy, typed_at)
    assert spy['last_user'] == 0  # deadline T0 + 200 not reached: nothing was judged
    clock.wall += 200  # T0 + 260
    tick(spy, typed_at)
    assert spy['last_user'] >= 1 and sorted(set(spy['woken'])) == ['c1', 'c2']


# the unlink observation clock ----------------------------------------------------------------------------------
@pytest.fixture
def unlinking(world, spy, clock):  # noqa: F811
    """c4 waits on a card no board knows (a missing entry): its unlink clock ages on every healthy tick."""
    world['led'].write('c4', batch=['t_gone'], open=['t_gone'])
    sqlite3.connect(world['state']).close()
    from hermes_state import SessionDB
    db = SessionDB(db_path=world['state'])
    db.create_session('c4', source='cli')
    db.close()
    return world


def entry(world):
    row = next(r for r in world['led'].pending() if r['session_id'] == 'c4')
    return row['unlinked']['t_gone']


@pytest.mark.parametrize('memo', [True, False], ids=['clock-only', 'every-tick-full'])
def test_the_unlink_clock_is_written_every_tick_with_equal_output_and_nothing_else(unlinking, spy, clock, memo):
    world = unlinking
    works = []
    for step in range(6):
        if not memo:
            plugin._state.pop('host_memo', None)
            plugin._state.pop('host_clock', None)
        spy['reset']()
        before = counters()
        tick(spy, lambda: clock.wall - 10)
        works.append((dict(world['work']), list(spy['writes']), counters()['skips'] - before['skips']))
        clock.wall += 30
    e = entry(world)
    # the first pass starts the clock, each later one adds the 30 s since the previous (the same either way)
    assert (e['since'], e['observed'], e['at']) == (T0, 150.0, T0 + 150.0)
    assert e['clocks'][str(e['pid'])]['at'] == T0 + 150.0
    if memo:
        # ticks 4..6 (the inputs settled at tick 3) judge nothing, query nothing and write only the clock
        for work, writes, skips in works[3:]:
            assert work == {'reconcile_done': [], 'read_cards': 0}
            assert writes == [('c4', ['unlinked'])] and skips == 1
        assert la.counters()['chat-closeout.wake_tick_clock']['runs'] >= 3
    else:
        assert all(w['read_cards'] >= 1 for w, _, _ in works)


def test_a_clock_row_reaching_its_removal_runs_the_full_pass(unlinking, spy, clock, monkeypatch):
    world = unlinking
    monkeypatch.setattr(core, 'UNLINK_AFTER_S', 100.0)
    for _ in range(4):  # observed 0, 30, 60, 90 (the last ones are clock-only ticks)
        tick(spy, lambda: clock.wall - 10)
        clock.wall += 30
    assert 't_gone' in next(iter(world['led'].pending_for({'c4'})))['batch']
    spy['reset']()
    tick(spy, lambda: clock.wall - 10)  # observed 120 >= 100: the full pass takes c4's entry out of its batch
    assert world['work']['read_cards'] >= 1
    assert 't_gone' not in (next(iter(world['led'].pending_for({'c4'})), {}).get('batch') or [])


def test_a_card_appearing_ends_the_clock_on_the_next_tick(unlinking, spy, clock):
    world = unlinking
    for _ in range(4):
        tick(spy, lambda: clock.wall - 10)
        clock.wall += 30
    with sqlite3.connect(world['board']) as conn:
        conn.execute("INSERT INTO tasks VALUES ('t_gone', 'back', 'running', NULL, NULL)")
    spy['reset']()
    tick(spy, lambda: clock.wall - 10)
    assert world['work']['read_cards'] >= 1
    row = next(r for r in world['led'].pending() if r['session_id'] == 'c4')
    assert 't_gone' not in (row.get('unlinked') or {})  # found again: forgets its missing time


# owner wake and watchdog: unchanged inputs, no read -----------------------------------------------------------------
def _host_tick(spy, clock, typing):
    tick(spy, typing)  # wake_tick shares its inputs with the owner wake and the watchdog of the same tick


def test_the_watchdog_skips_on_unchanged_inputs_and_sees_a_change(world, spy, clock):  # noqa: F811
    world['led'].write('c1', batch=[], open=[])  # only c3 (working) is pending: the watchdog observes nothing
    world['led'].write('c2', batch=[], open=[])
    typing = lambda: clock.wall - 10  # noqa: E731
    for _ in range(2):
        _host_tick(spy, clock, typing)
        plugin.watchdog_tick()
        clock.wall += 30
    spy['reset']()
    _host_tick(spy, clock, typing)
    plugin._state.pop('pass_skip', None)
    assert plugin.watchdog_tick() == []
    assert plugin._state.pop('pass_skip', None) == 'watchdog_tick' and world['work']['read_cards'] == 0
    with sqlite3.connect(world['board']) as conn:  # a card moves: the next tick judges again
        conn.execute("UPDATE tasks SET status = 'running', completed_at = NULL WHERE id = 't_2'")
    clock.wall += 30
    _host_tick(spy, clock, typing)
    plugin.watchdog_tick()
    assert plugin._state.pop('pass_skip', None) is None


def test_the_watchdog_still_runs_when_a_flag_exists(world, spy, clock):  # noqa: F811
    world['led'].write('c1', batch=[], open=[])
    world['led'].write('c2', batch=[], open=[])
    typing = lambda: clock.wall - 10  # noqa: E731
    for _ in range(2):
        _host_tick(spy, clock, typing)
        plugin.watchdog_tick()
    world['led'].flag_once('chat-closeout:unseen:c1:1', clock.wall)
    plugin._state.pop('pass_skip', None)
    _host_tick(spy, clock, typing)
    plugin.watchdog_tick()
    assert plugin._state.pop('pass_skip', None) is None


def test_the_owner_wake_skips_on_unchanged_inputs_and_sees_a_change(world, spy, clock, monkeypatch):  # noqa: F811
    reads = []
    monkeypatch.setattr(plugin, '_parked_waits', lambda ksa, now: reads.append(1) or [])
    typing = lambda: clock.wall - 10  # noqa: E731
    _host_tick(spy, clock, typing)
    plugin.owner_wake_tick()
    clock.wall += 30
    _host_tick(spy, clock, typing)
    plugin._state.pop('pass_skip', None)
    assert plugin.owner_wake_tick() == [] and len(reads) == 1  # not read again
    assert plugin._state.pop('pass_skip', None) == 'owner_wake_tick'
    with sqlite3.connect(world['board']) as conn:
        conn.execute("UPDATE tasks SET status = 'blocked' WHERE id = 't_3'")
    clock.wall += 30
    _host_tick(spy, clock, typing)
    plugin.owner_wake_tick()
    assert len(reads) == 2


def test_the_owner_wake_judges_every_tick_while_a_card_is_due(world, spy, clock, monkeypatch):  # noqa: F811
    due = {'session': 'c1', 'id': 't_9', 'key': 'default/t_9', 'title': '', 'remote': False, 'gateway': None,
           'wait': {'agent_owned': True, 'premise': 'p', 'agent_owned_certain': True, 'waiting_since': clock.wall}}
    reads = []
    monkeypatch.setattr(plugin, '_parked_waits', lambda ksa, now: reads.append(1) or [due])
    monkeypatch.setattr(plugin.owner_wake, 'tick', lambda **kw: [])
    typing = lambda: clock.wall - 10  # noqa: E731
    for _ in range(3):
        _host_tick(spy, clock, typing)
        plugin.owner_wake_tick()
        clock.wall += 30
    assert len(reads) == 3


def test_a_waiting_card_going_stalled_is_judged_when_its_age_arrives(world, spy, clock, monkeypatch):  # noqa: F811
    waiting = {'session': 'c1', 'id': 't_9', 'key': 'default/t_9', 'title': '', 'remote': False, 'gateway': None,
               'wait': {'agent_owned': True, 'premise': 'p', 'agent_owned_certain': False, 'waiting_since': clock.wall,
                        'wait_state': 'waiting'}}
    reads = []
    monkeypatch.setattr(plugin, '_parked_waits', lambda ksa, now: reads.append(1) or [waiting])
    typing = lambda: clock.wall - 10  # noqa: E731
    for _ in range(3):
        _host_tick(spy, clock, typing)
        plugin.owner_wake_tick()
        clock.wall += 30
    assert len(reads) == 1  # an uncertain park only counts once stalled: nothing to do until then
    clock.wall += 7200
    _host_tick(spy, clock, typing)
    plugin.owner_wake_tick()
    assert len(reads) == 2
