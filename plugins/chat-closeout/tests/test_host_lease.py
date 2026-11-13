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


def test_busy_relay_is_retried_next_pass_and_expires_with_its_claim(tmp_path):
    wlog = owner_wake.WakeLog(tmp_path / 'owner-wake.db')
    _owner_tick(wlog, lambda *a, **k: 'refused', relay_refused=True)
    assert [o['result'] for o in owner_wake.take_relays(live={'chat'}, wake=lambda *a, **k: 'busy', log=wlog,
                                                         now=1030)] == ['busy']
    assert owner_wake.take_relays(live={'chat'}, wake=lambda *a, **k: 'busy', log=wlog, now=1060)
    assert owner_wake.take_relays(live={'chat'}, wake=lambda *a, **k: pytest.fail('expired'), log=wlog,
                                  now=1000 + owner_wake.CLAIM_TTL_S + 1) == []


def test_without_the_lease_a_refused_owner_wake_posts_nothing(tmp_path):
    wlog = owner_wake.WakeLog(tmp_path / 'owner-wake.db')
    _owner_tick(wlog, lambda *a, **k: 'refused', relay_refused=False)
    assert wlog.relays_for({'chat'}, 1010) == []


def test_residual_pass_takes_owner_relays_for_its_live_chats(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, live={'serve': {'chat'}})
    _owner_tick(w.wlog, lambda *a, **k: 'refused', relay_refused=True, now=__import__('time').time())
    w.run_as('serve', held=False)
    assert w.started() == [('serve', 'chat')]


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
