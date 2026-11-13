"""Host loop lease (design t_01a1d45d75851804 §2b): one process per Hermes home runs the ledger scan; every other
backend wakes only the chats live in it. Processes are simulated in one interpreter: each ``run_as`` swaps in that
process's lease verdict, live chat ids, waker and pid."""
import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('closeout_host_lease', ROOT / '__init__.py')
plugin = importlib.util.module_from_spec(spec)
plugin.__package__ = ''
spec.loader.exec_module(plugin)
core = plugin.core
owner_wake = plugin.owner_wake

DONE = {'t_one': {'status': 'done'}}
PIDS = {'gateway': 41001, 'serve': 41002, 'serve2': 41003}


class FakeLease:
    def __init__(self, held):
        self._held, self.beats = held, 0

    def held(self):
        return self._held

    def beat(self):
        self.beats += 1


class World:
    """Which process holds which chat. A wake starts a turn where the chat is live or nowhere live (resumed there),
    and is refused where another process holds it (``server._wake_session_turn``)."""

    def __init__(self, tmp_path, monkeypatch, live):
        self.mp, self.live, self.log, self.ticks = monkeypatch, live, [], []
        self.led = core.Ledger(tmp_path / 'ledger.db')
        self.wlog = owner_wake.WakeLog(tmp_path / 'owner-wake.db')
        monkeypatch.setattr(plugin, 'ledger', lambda: self.led)
        monkeypatch.setattr(plugin, 'wake_log', lambda: self.wlog)
        monkeypatch.setattr(plugin, '_lineage', lambda sid: [sid])
        monkeypatch.setattr(plugin, '_session_source', lambda sid: '')
        monkeypatch.setattr(plugin, '_session_gone', lambda sid: False)
        monkeypatch.setattr(plugin, '_moved_owner', lambda sid: None)
        monkeypatch.setattr(plugin, '_unseen_counts', lambda: {})
        monkeypatch.setattr(plugin, 'ensure_upgraded', lambda read=None: True)
        monkeypatch.setattr(plugin, '_pass_reader', lambda light=False, rows=None: (lambda ids: dict(DONE)))
        monkeypatch.setattr(core, 'last_user_at', lambda *a, **k: 0)
        monkeypatch.setattr(plugin, 'owner_wake_tick', lambda **kw: self.ticks.append(('owner_wake_tick', kw)))
        monkeypatch.setattr(plugin, 'watchdog_tick', lambda **kw: self.ticks.append(('watchdog_tick', kw)))
        self.leases = {}

    def waker(self, proc):
        def wake(sid, prompt, **kw):
            owner = next((p for p, ids in self.live.items() if sid in ids), None)
            result = 'refused' if owner not in (None, proc) else 'started'
            self.log.append((proc, sid, result))
            return result
        return wake

    def run_as(self, proc, held):
        lease = self.leases.setdefault(proc, FakeLease(held))
        lease._held = held
        with self.mp.context() as m:
            m.setattr(plugin, '_host_lease', lambda: lease)
            m.setattr(plugin, '_lease_relays', lambda: True)
            m.setattr(plugin, '_live_ids', lambda: set(self.live.get(proc, ())))
            m.setattr(plugin, '_server', lambda: types.SimpleNamespace(_wake_session_turn=self.waker(proc)))
            m.setattr(os, 'getpid', lambda: PIDS[proc])
            return plugin.run_pass()

    def started(self):
        return [(p, s) for p, s, r in self.log if r == 'started']


def settled(world, *sids):
    for sid in sids:
        world.led.write(sid, batch=['t_one'], open=['t_one'])


def test_chat_live_in_a_non_holder_gets_its_closeout_from_its_residual_pass(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, live={'gateway': set(), 'serve': {'warm'}})
    settled(w, 'warm', 'cold')
    assert w.run_as('serve', held=False) == 'residual'
    assert w.started() == [('serve', 'warm')]  # only its own live chat; the cold one is the holder's
    assert w.ticks == []  # owner wake and watchdog scans are the holder's
    assert w.leases['serve'].beats == 0
    assert w.led.get('cold')['claim_sig'] is None


def test_cold_chat_is_woken_once_by_the_holder(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, live={'gateway': set(), 'serve': set(), 'serve2': set()})
    settled(w, 'cold')
    for _ in range(3):
        assert w.run_as('serve', held=False) == 'residual'
        assert w.run_as('gateway', held=True) == 'host'
        assert w.run_as('serve2', held=False) == 'residual'
    assert w.started() == [('gateway', 'cold')]
    assert w.leases['gateway'].beats == 3  # lease.beat() once per host pass
    assert [name for name, _ in w.ticks] == ['owner_wake_tick', 'watchdog_tick'] * 3
    assert all(kw == {'relay_refused': True} for name, kw in w.ticks if name == 'owner_wake_tick')


def test_holder_refused_for_a_chat_live_elsewhere_does_not_hold_off_its_owner(tmp_path, monkeypatch):
    """The holder's wake of a chat live in a serve is refused (backoff); the serve's residual pass still wakes it."""
    w = World(tmp_path, monkeypatch, live={'gateway': set(), 'serve': {'warm'}})
    settled(w, 'warm')
    w.run_as('gateway', held=True)
    assert w.log == [('gateway', 'warm', 'refused')]
    assert float(w.led.get('warm')['retry_after']) > 0
    w.run_as('serve', held=False)
    assert w.started() == [('serve', 'warm')]
    w.run_as('gateway', held=True)
    w.run_as('serve', held=False)
    assert w.started() == [('serve', 'warm')]  # once


def test_own_refusal_still_backs_off(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, live={'serve2': {'warm'}, 'serve': {'warm'}})
    settled(w, 'warm')
    w.run_as('serve', held=False)  # refused: serve2 also holds it
    w.run_as('serve', held=False)
    assert w.log == [('serve', 'warm', 'refused')]


def test_old_core_without_lease_module_runs_todays_full_pass(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, live={'gateway': set(), 'serve': {'warm'}})
    settled(w, 'warm', 'cold')
    monkeypatch.setitem(sys.modules, 'hermes_cli.host_loop_lease', None)  # ImportError
    if 'hermes_cli' in sys.modules:
        monkeypatch.delattr(sys.modules['hermes_cli'], 'host_loop_lease', raising=False)
    monkeypatch.setattr(plugin, '_state', {})
    monkeypatch.setattr(plugin, '_live_ids', lambda: {'warm'})
    monkeypatch.setattr(plugin, '_server', lambda: types.SimpleNamespace(_wake_session_turn=w.waker('serve')))
    assert plugin._host_lease() is None
    assert plugin.run_pass() == 'full'
    assert sorted(w.started()) == [('serve', 'cold'), ('serve', 'warm')]
    assert w.ticks == [('owner_wake_tick', {'relay_refused': False}), ('watchdog_tick', {})]


def test_unreadable_live_ids_fall_back_to_the_full_pass(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, live={'serve': {'warm'}})
    settled(w, 'warm', 'cold')
    with monkeypatch.context() as m:
        m.setattr(plugin, '_host_lease', lambda: FakeLease(False))
        m.setattr(plugin, '_live_ids', lambda: None)
        m.setattr(plugin, '_server', lambda: types.SimpleNamespace(_wake_session_turn=w.waker('serve')))
        assert plugin.run_pass() == 'full'
    assert sorted(w.started()) == [('serve', 'cold'), ('serve', 'warm')]


def test_residual_pass_with_nothing_pending_reads_no_cards(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, live={'serve': {'idle'}})
    settled(w, 'cold')
    monkeypatch.setattr(plugin, '_pass_reader', lambda **kw: pytest.fail('residual built a card reader'))
    monkeypatch.setattr(plugin, '_unseen_counts', lambda: pytest.fail('residual read unseen counts'))
    assert w.run_as('serve', held=False) == 'residual'
    assert w.log == []


def test_pending_for_reads_only_the_named_pending_rows(tmp_path):
    led = core.Ledger(tmp_path / 'ledger.db')
    led.write('a', batch=['t_1'])
    led.write('b', batch=['t_2'])
    led.write('c', batch=[])
    assert [r['session_id'] for r in led.pending_for({'a', 'c', 'zz'})] == ['a']
    assert led.pending_for(set()) == []


# ---- owner wake: the holder relays a refused wake to the chat's own process -----------------------------------
def _parked(sid='chat', since=990.0):
    return [{'session': sid, 'key': 'default/t_p', 'id': 't_p', 'title': 'parked',
             'wait': {'agent_owned': True, 'premise': 'p1', 'waiting_on': 'nothing named', 'waiting_since': since}}]


def _owner_tick(wlog, wake, relay_refused, now=1000.0):
    return owner_wake.tick(cards=_parked(since=now - 10), wake=wake, log=wlog,
                           chat_info=lambda sid: {'source': 'desktop'},
                           supervise=lambda *a: pytest.fail('a live chat is never routed'),
                           last_user=lambda sid: 0, now=now, relay_refused=relay_refused)


def test_refused_owner_wake_is_relayed_to_the_process_holding_the_chat(tmp_path):
    wlog = owner_wake.WakeLog(tmp_path / 'owner-wake.db')
    calls = []
    _owner_tick(wlog, lambda *a, **k: calls.append(('holder', a[0])) or 'refused', relay_refused=True)
    assert owner_wake.take_relays(live={'other'}, wake=lambda *a, **k: pytest.fail('not live here'),
                                  log=wlog, now=1010) == []
    out = owner_wake.take_relays(live={'chat'}, wake=lambda *a, **k: calls.append(('owner', a[0], a[1])) or 'started',
                                 log=wlog, now=1030)
    assert [o['result'] for o in out] == ['started']
    assert calls[0] == ('holder', 'chat') and calls[1][:2] == ('owner', 'chat')
    assert owner_wake.TAG in calls[1][2]  # the host pass's supervise prompt
    assert ('default/t_p', 'p1', 'chat', 'started', 'chat', None) in wlog.rows()
    assert owner_wake.take_relays(live={'chat'}, wake=lambda *a, **k: pytest.fail('taken once'), log=wlog,
                                  now=1060) == []
    _owner_tick(wlog, lambda *a, **k: pytest.fail('premise already woke'), relay_refused=True, now=5000)


def test_busy_relay_is_dropped_and_its_claims_released_so_the_next_host_pass_re_decides(tmp_path):
    """N1: a busy relayed wake is not replayed for up to CLAIM_TTL_S with a possibly stale premise. It is dropped
    and its claims released (as ``tick``'s own busy path does), so the next host pass re-checks ``due(card)``."""
    wlog = owner_wake.WakeLog(tmp_path / 'owner-wake.db')
    _owner_tick(wlog, lambda *a, **k: 'refused', relay_refused=True)
    assert [o['result'] for o in owner_wake.take_relays(live={'chat'}, wake=lambda *a, **k: 'busy', log=wlog,
                                                         now=1030)] == ['busy']
    assert wlog.relays_for({'chat'}, 1031) == []
    assert not wlog.fired('default/t_p', 'p1', 1031)
    # the card unparked meanwhile: the next host pass wakes nobody
    owner_wake.tick(cards=[], wake=lambda *a, **k: pytest.fail('card no longer due'), log=wlog,
                    chat_info=lambda sid: {'source': 'desktop'}, supervise=lambda *a: pytest.fail('no route'),
                    last_user=lambda sid: 0, now=1060, relay_refused=True)
    assert owner_wake.take_relays(live={'chat'}, wake=lambda *a, **k: pytest.fail('dropped'), log=wlog,
                                  now=1061) == []
    # still parked: the host pass re-decides and wakes again at once (not after CLAIM_TTL_S)
    calls = []
    _owner_tick(wlog, lambda *a, **k: calls.append(a[0]) or 'refused', relay_refused=True, now=1090)
    assert calls == ['chat']


def test_without_the_lease_a_refused_owner_wake_posts_nothing(tmp_path):
    wlog = owner_wake.WakeLog(tmp_path / 'owner-wake.db')
    _owner_tick(wlog, lambda *a, **k: 'refused', relay_refused=False)
    assert wlog.relays_for({'chat'}, 1010) == []


def test_residual_pass_takes_owner_relays_for_its_live_chats(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, live={'serve': {'chat'}})
    _owner_tick(w.wlog, lambda *a, **k: 'refused', relay_refused=True, now=__import__('time').time())
    w.run_as('serve', held=False)
    assert w.started() == [('serve', 'chat')]


# ---- lease handoff (N2): the new holder does not wait out what the old holder recorded for its own chats -------
def test_new_holder_retries_its_live_chat_over_the_old_holders_refusal(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, live={'gateway': {'warm'}, 'serve': set()})
    settled(w, 'warm')
    assert w.run_as('serve', held=True) == 'host'  # serve held the lease; the chat is live in the gateway
    assert w.log == [('serve', 'warm', 'refused')]
    assert w.run_as('gateway', held=True) == 'host'  # lease handed to the gateway
    assert w.started() == [('gateway', 'warm')]
    w.run_as('gateway', held=True)
    assert w.started() == [('gateway', 'warm')]  # once


def test_new_holder_still_backs_off_its_own_refusal(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, live={'serve': {'warm'}, 'gateway': {'warm'}})
    settled(w, 'warm')
    w.run_as('gateway', held=True)  # refused: serve also holds it
    w.run_as('gateway', held=True)
    assert w.log == [('gateway', 'warm', 'refused')]


def test_new_holder_takes_relays_the_old_holder_posted_for_its_live_chats(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, live={'gateway': {'chat'}, 'serve': set()})
    _owner_tick(w.wlog, lambda *a, **k: 'refused', relay_refused=True, now=__import__('time').time())  # old holder
    assert w.run_as('gateway', held=True) == 'host'
    assert w.started() == [('gateway', 'chat')]
    assert w.wlog.relays_for({'chat'}, __import__('time').time()) == []


# ---- reload (N3): the previous module's lease is released, not left to core's watchdog -------------------------
def _fresh_module(name):
    s = importlib.util.spec_from_file_location(name, ROOT / '__init__.py')
    m = importlib.util.module_from_spec(s)
    m.__package__ = ''
    s.loader.exec_module(m)
    return m


class FakeCtx:
    def __init__(self):
        self.unloads = []

    def register_hook(self, *a, **k):
        pass

    def on_unload(self, cb):
        self.unloads.append(cb)


def _real_lease(tmp_path, monkeypatch):
    hll = pytest.importorskip('hermes_cli.host_loop_lease')
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.delenv(hll.KILL_SWITCH_ENV, raising=False)
    return hll


def test_plugin_unload_releases_the_lease_so_the_reloaded_module_holds_it_at_once(tmp_path, monkeypatch):
    _real_lease(tmp_path, monkeypatch)
    monkeypatch.setenv('HERMES_KANBAN_TASK', 't_fixture')  # no waker thread
    old, new = _fresh_module('closeout_reload_old'), _fresh_module('closeout_reload_new')
    try:
        assert old._host_lease().held()
        ctx = FakeCtx()
        old.register(ctx)
        assert not new._host_lease().held()  # the old module's fd still holds the flock
        for cb in ctx.unloads:
            cb()
        assert new._host_lease().held()
    finally:
        for m in (old, new):
            if m._state.get('lease') is not None:
                m._state['lease'].release()


def test_stopped_waker_thread_releases_its_modules_lease(tmp_path, monkeypatch):
    """A reload that only stops the old waker (``_stop_stale_threads``) also frees that module's lease."""
    _real_lease(tmp_path, monkeypatch)
    monkeypatch.setenv('CHAT_CLOSEOUT_FIRST_S', '3600')
    old, new = _fresh_module('closeout_thread_old'), _fresh_module('closeout_thread_new')
    try:
        assert old._host_lease().held()
        old.start_thread()
        t_old = old._state['thread']
        new.start_thread()  # stops the old module's thread
        t_old.join(5)
        assert not t_old.is_alive()
        assert new._host_lease().held()
    finally:
        for m in (old, new):
            if m._state.get('stop') is not None:
                m._state['stop'].set()
            if m._state.get('lease') is not None:
                m._state['lease'].release()


def test_unload_between_host_lease_and_held_does_not_orphan_a_reacquired_lease(tmp_path, monkeypatch):
    """Review 5891095798: ``_unload`` lands after ``run_pass``'s ``_host_lease()`` but before its ``held()``. That
    ``held()`` re-opens and re-acquires the released lease; it must be given straight back (no host pass, fd
    closed), not left held until core's 3x-interval watchdog with the reloaded module locked out."""
    _real_lease(tmp_path, monkeypatch)
    monkeypatch.setenv('HERMES_KANBAN_TASK', 't_fixture')  # no waker thread
    old, new = _fresh_module('closeout_race_old'), _fresh_module('closeout_race_new')
    passes = []
    monkeypatch.setattr(old, '_host_pass', lambda *a, **k: passes.append(a))
    monkeypatch.setattr(old, '_live_ids', lambda: set())
    ctx = FakeCtx()
    old.register(ctx)
    real = old._host_lease
    grabbed = []

    def host_lease_then_unload():
        lease = real()
        grabbed.append(lease)
        for cb in ctx.unloads:  # the reload's unload, interleaved before run_pass calls held()
            cb()
        return lease

    monkeypatch.setattr(old, '_host_lease', host_lease_then_unload)
    try:
        ran = old.run_pass()
        old._release_lease()  # the stopped loop's finally
        lease = grabbed[0]
        assert not lease._held  # given back, not orphaned until core's watchdog
        assert lease._fd is None  # no leaked fd
        assert (ran, passes) == ('stopped', [])  # an unloaded module runs no host pass
        assert new._host_lease().held()  # the reloaded module takes it at once
        assert old.run_pass() == 'stopped' and len(grabbed) == 1  # later passes build no fresh lease
    finally:
        for m in (old, new):
            if m._state.get('lease') is not None:
                m._state['lease'].release()
        for lease in grabbed:
            lease.release()


# ---- wiring -----------------------------------------------------------------------------------------------------
def test_lease_role_from_the_hermes_subcommand(monkeypatch):
    for argv, role in ((['hermes', 'gateway', 'run'], 'gateway'), (['hermes', '--profile', 'x', 'serve'], 'serve'),
                       (['hermes', 'dashboard', '--no-open'], 'dashboard'), (['python'], 'serve')):
        monkeypatch.setattr(sys, 'argv', argv)
        assert plugin._lease_role() == role


def test_real_core_lease_elects_one_holder(tmp_path, monkeypatch):
    hll = pytest.importorskip('hermes_cli.host_loop_lease')
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.delenv(hll.KILL_SWITCH_ENV, raising=False)
    monkeypatch.setattr(plugin, '_state', {})
    mine = plugin._host_lease()
    assert mine is not None and mine.name == 'chat-closeout'
    other = hll.HostLoopLease('chat-closeout', 'serve', interval=30)
    try:
        assert mine.held()
        assert not other.held()
        assert plugin._lease_relays()
        monkeypatch.setenv(hll.KILL_SWITCH_ENV, '0')
        assert other.held() and not plugin._lease_relays()
    finally:
        mine.release()
        other.release()
