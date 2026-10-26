"""Review close-out -> reviewer reopens -> done -> final notice. The sidebar, hook and waker must agree."""
import importlib.util
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('closeout_plugin_consistency', ROOT / '__init__.py')
plugin = importlib.util.module_from_spec(spec)
plugin.__package__ = ''
spec.loader.exec_module(plugin)
core = plugin.core

REVIEW = {'status': 'review'}
RUNNING = {'status': 'running'}


def done(at):
    return {'status': 'done', 'completed_at': at}


def _waker(tmp_path, monkeypatch):
    led = core.Ledger(tmp_path / 'ledger.db')
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setattr(plugin, '_lineage', lambda sid: [sid])
    calls = []

    def tick(cards, now, proof=None):
        def wake(sid, *a, **k):
            calls.append(sid)
            return 'started'
        return plugin.wake_tick(now=now, wake=wake, unseen={}, last_user=lambda _: 0, read=lambda _: cards,
                                proof=proof)
    return led, calls, tick


def _review_then_reopened(led, tick, cards):
    """Waker wakes at review; the reviewer reopens the card while the close-out turn runs; the turn ends."""
    led.write('chat', batch=['t_x'], open=['t_x'])
    cards['t_x'] = REVIEW
    assert tick(cards, 1000)
    cards['t_x'] = RUNNING                      # reviewer reopened mid close-out
    core.record_turn_end(led, 'chat', [], lambda _: cards, now=1060)


def test_reopened_during_closeout_does_not_stamp_done(tmp_path, monkeypatch):
    led, calls, tick = _waker(tmp_path, monkeypatch)
    cards = {}
    _review_then_reopened(led, tick, cards)
    row = led.get('chat')
    assert not row['done_sig'] and row['covered'] == [] and row['batch'] == ['t_x']


def test_review_to_running_to_done_gets_exactly_one_final_closeout(tmp_path, monkeypatch):
    led, calls, tick = _waker(tmp_path, monkeypatch)
    cards = {}
    _review_then_reopened(led, tick, cards)
    cards['t_x'] = done(1458)
    assert core.pending_closeouts(led, lambda _: cards) == {'chat': ['t_x']}
    assert tick(cards, 1500) and calls == ['chat', 'chat']
    core.record_turn_end(led, 'chat', [], lambda _: cards, now=1520)       # final notice turn ends
    assert led.get('chat')['batch'] == []
    assert core.pending_closeouts(led, lambda _: cards) == {}
    for now in (1600, 1700, 5000):
        assert not tick(cards, now)
    assert calls == ['chat', 'chat']


def _stale_row(led):
    """The live historical row: done_sig stamped before the card finished, nothing covered."""
    cards = {'t_x': done(1458)}
    key = core.closeout_key({'batch': ['t_x'], 'covered': []}, cards)
    led.write('chat', batch=['t_x'], open=[], covered=[], done_sig=key, settled_sig=key, done_at=1061.0,
              stamped_at=1475.0)
    return cards, key


def test_shared_decision_sidebar_hook_and_waker_agree(tmp_path, monkeypatch):
    led, calls, tick = _waker(tmp_path, monkeypatch)
    cards, key = _stale_row(led)
    row = led.get('chat')
    # completed after the stamped close-out and no proof it was reported: really pending everywhere
    assert core.pending_key(row, cards) is None            # the raw ledger decision stays conservative...
    fixed = core.reconcile_done(row, cards)
    assert fixed.get('done_sig') is None and 'done_sig' in fixed   # ...but reconcile reopens it (done after done_at)
    assert core.pending_closeouts(led, lambda _: cards) == {'chat': ['t_x']}
    assert tick(cards, 2000) and calls == ['chat']


def test_proven_acknowledged_row_is_reconciled_not_woken(tmp_path, monkeypatch):
    led, calls, tick = _waker(tmp_path, monkeypatch)
    cards, key = _stale_row(led)
    proof = lambda sid, tid, card: tid == 't_x'
    assert core.pending_closeouts(led, lambda _: cards, proof=proof) == {}
    assert not tick(cards, 2000, proof=proof)
    assert led.get('chat')['batch'] == [] and calls == []


def test_unproven_unknown_remote_state_is_preserved(tmp_path, monkeypatch):
    led, calls, tick = _waker(tmp_path, monkeypatch)
    key = core.closeout_key({'batch': ['g/p/b/t_r'], 'covered': []}, {'g/p/b/t_r': {'status': 'done'}})
    led.write('chat', batch=['g/p/b/t_r'], covered=[], done_sig=key, done_at=10.0)
    cards = {'g/p/b/t_r': {'status': 'running'}}             # reopened / unsettled: untouched
    assert core.reconcile_done(led.get('chat'), cards) == {}
    assert core.pending_closeouts(led, lambda _: cards) == {}
    assert led.get('chat')['batch'] == ['g/p/b/t_r']


def test_card_completed_before_the_closeout_is_acknowledged(tmp_path, monkeypatch):
    led, calls, tick = _waker(tmp_path, monkeypatch)
    cards = {'t_x': done(900)}
    key = core.closeout_key({'batch': ['t_x'], 'covered': []}, cards)
    led.write('chat', batch=['t_x'], covered=[], done_sig=key, done_at=1061.0)
    assert core.pending_closeouts(led, lambda _: cards) == {}
    assert not tick(cards, 2000)
    assert led.get('chat')['batch'] == []


def test_prehook_briefs_final_notice_when_done_sig_is_stale(tmp_path, monkeypatch):
    led, calls, tick = _waker(tmp_path, monkeypatch)
    cards, key = _stale_row(led)
    monkeypatch.setattr(plugin, '_eligible', lambda *a, **k: True)
    monkeypatch.setattr(plugin, 'read_cards', lambda ids: cards)
    monkeypatch.setattr(plugin, 'session_proof', lambda sid, tid, card: False)
    out = plugin.pre_llm_call(session_id='chat')
    assert out and 't_x' in out['context']


def test_restart_race_late_hook_after_waker_started_does_not_double_close(tmp_path, monkeypatch):
    """Waker marked the batch covered + closing; a late pre_llm_call (restart) must not brief again."""
    led, calls, tick = _waker(tmp_path, monkeypatch)
    cards = {'t_x': done(900)}
    led.write('chat', batch=['t_x'], open=['t_x'])
    assert tick(cards, 1000)
    monkeypatch.setattr(plugin, '_eligible', lambda *a, **k: True)
    monkeypatch.setattr(plugin, 'read_cards', lambda ids: cards)
    assert plugin.pre_llm_call(session_id='chat') is None
    core.record_turn_end(led, 'chat', [], lambda _: cards, now=1020)
    assert not tick(cards, 1100) and calls == ['chat']


def test_ack_proof_from_transcript(tmp_path):
    db = tmp_path / 'state.db'
    c = sqlite3.connect(db)
    c.execute('create table messages (id integer primary key, session_id text, role text, content text, '
              'timestamp real, display_kind text, finish_reason text DEFAULT "stop", '
              'tool_calls text, active integer DEFAULT 1)')
    rows = [('s', 'user', '✔ [default] @default Kanban t_x done — x', 1462.0),
            ('s', 'assistant', 'Confirmed: final review passed', 1473.0)]
    for r in rows:
        c.execute('insert into messages (session_id, role, content, timestamp) values (?,?,?,?)', r)
    c.commit()
    c.close()
    assert core.ack_proven([db], ['s'], 't_x', 1458)
    assert not core.ack_proven([db], ['s'], 't_x', 1470)       # notice predates completion
    assert not core.ack_proven([db], ['s'], 't_other', 1458)


def test_acknowledged_done_card_leaves_a_batch_that_still_has_running_work(tmp_path):
    led = core.Ledger(tmp_path / 'ledger.db')
    cards = {'t_x': done(1458), 't_y': RUNNING}
    led.write('chat', batch=['t_x', 't_y'], open=['t_y'], covered=[])
    proof = lambda sid, tid, card: tid == 't_x'
    assert core.reconcile_done(led.get('chat'), cards) == {}
    assert core.pending_closeouts(led, lambda _: cards, proof=proof) == {}
    fix = core.reconcile_done(led.get('chat'), cards, lambda t, c: proof('chat', t, c))
    assert fix['batch'] == {'t_y'} and fix['covered'] == set()


def test_running_closeout_turn_is_never_reconciled_underneath(tmp_path):
    cards = {'t_x': done(1458)}
    row = {'batch': ['t_x'], 'covered': [], 'closing': ['t_x']}
    assert core.reconcile_done(row, cards, lambda t, c: True) == {}
