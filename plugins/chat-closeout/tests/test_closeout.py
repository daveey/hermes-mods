import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('closeout_plugin_tests', ROOT / '__init__.py')
plugin = importlib.util.module_from_spec(spec)
plugin.__package__ = ''
spec.loader.exec_module(plugin)
core = plugin.core


def test_missing_card_is_not_completion():
    assert core.settled_sig(['t_missing'], {}) is None


def test_promise_detection_ignores_code_and_quotes():
    assert core.find_promise("Card t_1 is running now; I'll report back when it finishes.")
    assert core.find_promise("Done: the fix is merged.") is None
    assert core.find_promise("> I'll report back\n`running now`") is None


def test_midturn_completion_needs_closeout(tmp_path):
    led = core.Ledger(tmp_path / 'ledger.db')
    core.record_turn_end(led, 'chat', ['t_one'], lambda _: {'t_one': {'status': 'running'}}, now=1)
    core.record_turn_end(led, 'chat', [], lambda _: {'t_one': {'status': 'done'}}, now=2)
    assert led.get('chat')['batch'] == ['t_one']
    assert not led.get('chat')['done_sig']


def test_observed_handoff_clears_batch(tmp_path):
    led = core.Ledger(tmp_path / 'ledger.db')
    cards = {'t_one': {'status': 'done'}}
    led.write('chat', batch=['t_one'], open=['t_one'])
    sig = core.settled_sig(['t_one'], cards)
    key = core.closeout_key(led.get('chat'), cards)
    core.record_turn_end(led, 'chat', [], lambda _: cards, now=2, observed_sig=sig)
    assert led.get('chat')['batch'] == []
    assert led.get('chat')['done_sig'] == key


def test_wake_once_and_quiet_period(tmp_path, monkeypatch):
    led = core.Ledger(tmp_path / 'ledger.db')
    led.write('chat', batch=['t_one'], open=['t_one'])
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setattr(plugin, '_lineage', lambda sid: [sid])
    calls = []
    def wake(*args, **kwargs):
        calls.append(args)
        return 'started'
    args = dict(now=1000, wake=wake, read=lambda _: {'t_one': {'status': 'done'}})
    assert not plugin.wake_tick(**args, unseen={'chat': 1}, last_user=lambda _: 0)
    assert not plugin.wake_tick(**args, unseen={}, last_user=lambda _: 900)
    assert plugin.wake_tick(**args, unseen={}, last_user=lambda _: 0)
    assert not plugin.wake_tick(**args, unseen={}, last_user=lambda _: 0)
    assert len(calls) == 1


def test_lint_only_passes_actual_backing(monkeypatch):
    monkeypatch.delenv('HERMES_KANBAN_TASK', raising=False)
    monkeypatch.setattr(plugin, '_backed', lambda _: False)
    assert plugin.pre_final_gate(session_id='chat', final_response="I'll report back")['hold']
    monkeypatch.setattr(plugin, '_backed', lambda _: True)
    assert plugin.pre_final_gate(session_id='chat', final_response="I'll report back") is None


def test_watchdog_stale_unseen(tmp_path, monkeypatch, caplog):
    led = core.Ledger(tmp_path / 'ledger.db')
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    posts = []
    monkeypatch.setitem(plugin._state, 'mono_seen', {})
    args = dict(unseen={'chat': 1}, post=lambda *args: posts.append(args), force=True)
    assert not plugin.watchdog_tick(now=1, mono=1, **args)
    assert plugin.watchdog_tick(now=1802, mono=1802, **args)
    assert not plugin.watchdog_tick(now=1900, mono=1900, **args)
    assert len(posts) == 1
    assert 'watchdog' in caplog.text


def test_watchdog_ignores_wall_time_that_passed_asleep(tmp_path, monkeypatch):
    """A lid sleep moves wall time but not the monotonic clock: no false 'stuck' flag on wake."""
    led = core.Ledger(tmp_path / 'ledger.db')
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setitem(plugin._state, 'mono_seen', {})
    posts = []
    args = dict(unseen={'chat': 1}, post=lambda *a: posts.append(a), force=True)
    plugin.watchdog_tick(now=1, mono=1, **args)
    assert not plugin.watchdog_tick(now=8 * 3600, mono=60, **args)  # 8 h asleep, 1 min awake
    assert posts == []


def test_watchdog_retries_a_failed_inbox_post(tmp_path, monkeypatch):
    led = core.Ledger(tmp_path / 'ledger.db')
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setitem(plugin._state, 'mono_seen', {})
    def broken(*a):
        raise OSError('inbox down')
    posts = []
    plugin.watchdog_tick(now=1, mono=1, unseen={'chat': 1}, post=broken, force=True)
    assert not plugin.watchdog_tick(now=1802, mono=1802, unseen={'chat': 1}, post=broken, force=True)
    assert plugin.watchdog_tick(now=1900, mono=1900, unseen={'chat': 1}, post=lambda *a: posts.append(a), force=True)
    assert len(posts) == 1


def test_watchdog_flags_a_settled_batch_with_no_closeout(tmp_path, monkeypatch):
    led = core.Ledger(tmp_path / 'ledger.db')
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setitem(plugin._state, 'mono_seen', {})
    cards = {'t_one': {'status': 'done'}}
    sig = core.closeout_key({'batch': ['t_one']}, cards)
    led.write('chat', batch=['t_one'], open=[], settled_sig=sig, settled_at=1.0)
    posts = []
    args = dict(unseen={}, post=lambda *a: posts.append(a), force=True, read=lambda _: cards)
    assert not plugin.watchdog_tick(now=2, mono=2, **args)
    assert plugin.watchdog_tick(now=1900, mono=1900, **args) == [f'chat-closeout:no-closeout:chat:{sig}']


def test_unreadable_unseen_counts_never_wake(tmp_path, monkeypatch):
    led = core.Ledger(tmp_path / 'ledger.db')
    led.write('chat', batch=['t_one'], open=['t_one'])
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setattr(plugin, '_lineage', lambda sid: [sid])
    monkeypatch.setattr(plugin, '_unseen_counts', lambda: None)
    calls = []
    assert plugin.wake_tick(now=1000, wake=lambda *a, **k: calls.append(a) or 'started', last_user=lambda _: 0,
                            read=lambda _: {'t_one': {'status': 'done'}}) == []
    assert calls == []


def test_ledger_stamps_and_clears_waiting_on(tmp_path):
    led = core.Ledger(tmp_path / 'ledger.db')
    cards = {'t_one': {'status': 'running'}, 't_two': {'status': 'todo'}}
    out = core.record_turn_end(led, 'chat', ['t_one', 't_two'], lambda _: cards, now=1)
    assert out['open'] == ['t_one', 't_two'] and led.get('chat')['open'] == ['t_one', 't_two']
    cards['t_one']['status'] = cards['t_two']['status'] = 'done'
    sig = core.settled_sig(['t_one', 't_two'], cards)
    core.record_turn_end(led, 'chat', [], lambda _: cards, now=2, observed_sig=sig)
    assert led.get('chat')['open'] == [] and led.get('chat')['batch'] == []
    assert core.record_turn_end(led, 'quiet', [], lambda _: {}, now=3) == {'open': [], 'batch': []}
    assert led.get('quiet') is None


def test_sidebar_source_waiting_until_closeout_and_blocked_stays_red(tmp_path):
    """Sidebar states: open cards come from the boards; a settled batch without its close-out stays 'waiting';
    after the close-out it drops out; a card blocked on the user keeps its batch but is not 'waiting'."""
    led = core.Ledger(tmp_path / 'ledger.db')
    cards = {'t_one': {'status': 'done'}}
    led.write('chat', batch=['t_one'], open=[])
    assert core.pending_closeouts(led, lambda _: cards) == {'chat': ['t_one']}
    sig = core.settled_sig(['t_one'], cards)
    core.record_turn_end(led, 'chat', [], lambda _: cards, now=2, observed_sig=sig)
    assert core.pending_closeouts(led, lambda _: cards) == {}
    blocked = {'t_b': {'status': 'blocked', 'block_kind': 'needs_input'}}
    led.write('red', batch=['t_b'], open=['t_b'])
    sig = core.settled_sig(['t_b'], blocked)
    core.record_turn_end(led, 'red', ['t_b'], lambda _: blocked, now=3, observed_sig=sig)
    assert led.get('red')['batch'] == ['t_b']  # kept: the user still owes it
    assert core.pending_closeouts(led, lambda _: blocked) == {}  # closed out; the row is red via kanban_blocked
    running = {'t_r': {'status': 'running'}}
    led.write('busy', batch=['t_r'], open=['t_r'])
    assert 'busy' not in core.pending_closeouts(led, lambda _: running)  # the boards count it as open


def test_blocked_human_vs_machine():
    assert core.is_settled({'status': 'blocked', 'block_kind': 'needs_input'})
    assert not core.is_settled({'status': 'blocked', 'block_kind': 'time'})
    assert not core.is_settled({'status': 'triage', 'block_kind': None})


def test_wake_exception_releases_claim(tmp_path, monkeypatch):
    led = core.Ledger(tmp_path / 'ledger.db')
    led.write('chat', batch=['t_one'], open=['t_one'])
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setattr(plugin, '_lineage', lambda sid: [sid])
    def wake(*args, **kwargs):
        raise RuntimeError('temporary backend failure')
    plugin.wake_tick(now=1000, wake=wake, unseen={}, last_user=lambda _: 0,
                     read=lambda _: {'t_one': {'status': 'done'}})
    assert not led.get('chat')['claim_sig']
    assert led.get('chat')['batch'] == ['t_one']


def test_wrong_backend_missing_does_not_discard_batch(tmp_path, monkeypatch):
    led = core.Ledger(tmp_path / 'ledger.db')
    led.write('chat', batch=['t_one'], open=['t_one'])
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setattr(plugin, '_lineage', lambda sid: [sid])
    plugin.wake_tick(now=1000, wake=lambda *a, **kw: 'missing', unseen={}, last_user=lambda _: 0,
                     read=lambda _: {'t_one': {'status': 'done'}})
    assert led.get('chat')['batch'] == ['t_one']
    assert not led.get('chat')['done_sig']


def test_hooks_only_acknowledge_handoff_seen_at_start(tmp_path, monkeypatch):
    monkeypatch.delenv('HERMES_KANBAN_TASK', raising=False)
    led = core.Ledger(tmp_path / 'ledger.db')
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setattr(plugin, 'open_card_ids', lambda _: [])
    cards = {'t_one': {'status': 'running'}}
    monkeypatch.setattr(plugin, 'read_cards', lambda _: cards)
    led.write('chat', batch=['t_one'], open=['t_one'])
    assert plugin.pre_llm_call(session_id='chat') is None
    cards['t_one']['status'] = 'done'
    plugin.post_llm_call(session_id='chat')
    assert led.get('chat')['batch']
    assert plugin.pre_llm_call(session_id='chat')['context']
    plugin.post_llm_call(session_id='chat')
    assert not led.get('chat')['batch']


# ---- one wake per batch (review round 1: three wakes in 90 s for one chat) -------------------------------------
def _waker(tmp_path, monkeypatch):
    led = core.Ledger(tmp_path / 'ledger.db')
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setattr(plugin, '_lineage', lambda sid: [sid])
    calls = []
    def wake(sid, *a, **k):
        calls.append(sid)
        return 'started'
    def tick(cards, now):
        return plugin.wake_tick(now=now, wake=wake, unseen={}, last_user=lambda _: 0, read=lambda _: cards)
    return led, calls, tick


def test_settled_status_churn_does_not_wake_again(tmp_path, monkeypatch):
    """(a) blocked -> archived (and done -> archived) is settled -> settled: no second close-out."""
    led, calls, tick = _waker(tmp_path, monkeypatch)
    cards = {'t_b': {'status': 'blocked', 'block_kind': 'needs_input'}, 't_d': {'status': 'done'}}
    led.write('chat', batch=['t_b', 't_d'], open=['t_b'])
    assert tick(cards, 1000)
    core.record_turn_end(led, 'chat', ['t_b'], lambda _: cards, now=1010)  # the woken turn ends
    assert led.get('chat')['batch'] == ['t_b'] and led.get('chat')['covered'] == ['t_b']
    cards['t_b'] = {'status': 'archived'}
    cards['t_d'] = {'status': 'archived'}
    for now in (1040, 1070, 2000):
        assert not tick(cards, now)
    assert calls == ['chat']
    assert led.get('chat')['batch'] == []  # reported + archived: the row is finished


def test_closeout_turn_creating_a_needs_input_card_does_not_wake_again(tmp_path, monkeypatch):
    """(b) the close-out turn itself creates a card blocked on the user: it is born settled, but it was made by the
    close-out, so it is covered at that turn's end and never re-triggers a wake."""
    led, calls, tick = _waker(tmp_path, monkeypatch)
    cards = {'t_one': {'status': 'done'}}
    led.write('chat', batch=['t_one'], open=['t_one'])
    assert tick(cards, 1000)
    cards['t_new'] = {'status': 'blocked', 'block_kind': None}
    out = core.record_turn_end(led, 'chat', ['t_new'], lambda _: cards, now=1020)
    assert out['closeout'] and 't_new' in led.get('chat')['covered']
    for now in (1050, 1080, 1500):
        assert not tick(cards, now)
    assert calls == ['chat']
    assert core.pending_closeouts(led, lambda _: cards) == {}  # not grey 'waiting'; kanban_blocked paints it red


def test_live_182211_replay_wakes_exactly_once(tmp_path, monkeypatch):
    """(c) The live failure: two human-blocked cards, cleanup archives them, the close-out turn makes another
    blocked card. Exactly one wake, counted in the ledger."""
    led, calls, tick = _waker(tmp_path, monkeypatch)
    cards = {'t_5d': {'status': 'blocked', 'block_kind': None},
             't_5f': {'status': 'blocked', 'block_kind': 'needs_input'}}
    led.write('chat', batch=['t_5d', 't_5f'], open=['t_5d', 't_5f'])
    assert tick(cards, 1000)
    assert not tick(cards, 1005)               # the woken turn is still running
    cards['t_5d'] = {'status': 'archived'}     # cleanup mid-turn
    cards['t_d0'] = {'status': 'blocked', 'block_kind': None}  # created by the close-out turn
    core.record_turn_end(led, 'chat', ['t_5f', 't_d0'], lambda _: cards, now=1030)
    cards['t_5f'] = {'status': 'archived'}
    for now in range(1060, 1600, 30):
        tick(cards, now)
    assert calls == ['chat']
    assert led.get('chat')['wake_count'] == 1


def test_reopened_card_gets_a_new_closeout(tmp_path, monkeypatch):
    """Control: a covered blocked card that the user unblocks and that then finishes IS new work: one more wake."""
    led, calls, tick = _waker(tmp_path, monkeypatch)
    cards = {'t_b': {'status': 'blocked', 'block_kind': 'needs_input'}}
    led.write('chat', batch=['t_b'], open=['t_b'])
    assert tick(cards, 1000)
    core.record_turn_end(led, 'chat', ['t_b'], lambda _: cards, now=1010)
    cards['t_b'] = {'status': 'running'}
    assert not tick(cards, 1100)
    assert led.get('chat')['covered'] == []
    cards['t_b'] = {'status': 'done'}
    assert tick(cards, 1200)
    assert calls == ['chat', 'chat']


def test_hourly_wake_cap(tmp_path, monkeypatch, caplog):
    """A close-out that keeps creating fresh cards that settle can't loop: at most MAX_WAKES_PER_HOUR per chat."""
    led, calls, tick = _waker(tmp_path, monkeypatch)
    cards = {'t_0': {'status': 'done'}}
    led.write('chat', batch=['t_0'], open=[])
    now = 1000
    for i in range(1, 6):
        tick(cards, now)
        cards[f't_{i}'] = {'status': 'running'}
        core.record_turn_end(led, 'chat', [f't_{i}'], lambda _: cards, now=now + 5)
        cards[f't_{i}'] = {'status': 'done'}
        now += 60
    assert len(calls) == core.MAX_WAKES_PER_HOUR
    assert 'wakes/hour cap' in caplog.text
    tick(cards, now + 3600)  # the cap is per hour, not forever
    assert len(calls) == core.MAX_WAKES_PER_HOUR + 1


def test_live_notice_turn_creating_blocked_card_is_not_rebriefed(tmp_path, monkeypatch):
    """The pre_llm_call path (open chat, kanban notice turn) follows the same coverage rules."""
    monkeypatch.delenv('HERMES_KANBAN_TASK', raising=False)
    led = core.Ledger(tmp_path / 'ledger.db')
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    cards = {'t_one': {'status': 'done'}}
    monkeypatch.setattr(plugin, 'read_cards', lambda _: cards)
    opened = []
    monkeypatch.setattr(plugin, 'open_card_ids', lambda _: list(opened))
    led.write('chat', batch=['t_one'], open=[])
    assert plugin.pre_llm_call(session_id='chat')['context']
    cards['t_ask'] = {'status': 'blocked', 'block_kind': 'needs_input'}
    opened.append('t_ask')
    plugin.post_llm_call(session_id='chat')
    assert plugin.pre_llm_call(session_id='chat') is None
    assert led.get('chat')['batch'] == ['t_ask']


def test_failed_wake_undoes_coverage(tmp_path, monkeypatch):
    led = core.Ledger(tmp_path / 'ledger.db')
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setattr(plugin, '_lineage', lambda sid: [sid])
    cards = {'t_one': {'status': 'done'}}
    led.write('chat', batch=['t_one'], open=[])
    plugin.wake_tick(now=1000, wake=lambda *a, **k: 'busy', unseen={}, last_user=lambda _: 0, read=lambda _: cards)
    row = led.get('chat')
    assert row['covered'] == [] and row['closing'] is None and row['wake_count'] == 0
    assert plugin.wake_tick(now=1030, wake=lambda *a, **k: 'started', unseen={}, last_user=lambda _: 0,
                            read=lambda _: cards)
