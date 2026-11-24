"""The source producer reconciles only the chats whose own inputs moved, and the feed's timing is readable."""
import importlib.util
import json
import sys
import threading
import time
from pathlib import Path

import pytest

from test_feed import feedmod, make

WATCHED = [f'C{i}' for i in range(37)]


def setup():
    f, r, pushed, t = make()
    source = {'version': 1}
    calls = []
    r.reconcile_scoped = True
    r.reconciliation_stamp = lambda: source['version']
    r.reconcile_sources = lambda sessions: calls.append(list(sessions)) or set()
    r.panel_files_stamp = lambda: r.stamp
    r.panel_stamps = lambda ids: {s: 0 for s in ids}
    for s in WATCHED:
        r.links[s] = [{'id': f't_{s}', 'status': 'running', 'board': 'default', 'session_id': s}]
        r.cards[f't_{s}'] = {'detail': {'task': {'id': f't_{s}', 'status': 'running', 'session_id': s},
                                        'links': {'parents': [], 'children': []}, 'link_tasks': [], 'events': []},
                             'board': 'default'}
    return f, r, source, calls


def write(r, source, card=None, session=None):
    source['version'] += 1
    r.stamp += 1
    if card:
        r.events.append((card, session))


def test_source_writes_touching_one_chat_reconcile_that_chat_only():
    f, r, source, calls = setup()
    f.watch(WATCHED)
    f.tick()
    assert sorted(calls[0]) == sorted(WATCHED), 'first sight of a chat reconciles it'
    calls.clear()
    for _ in range(5):
        write(r, source, 't_C7', 'C7')
        f.tick()
    assert calls == [['C7']] * 5
    # writes that touch no watched chat's inputs reconcile nobody
    for _ in range(5):
        write(r, source)
        f.tick()
    assert calls == [['C7']] * 5


def test_a_lineage_change_admits_that_chat_without_any_card_event():
    f, r, source, calls = setup()
    f.watch(WATCHED)
    f.tick()
    calls.clear()
    r.lineages['C3'] = ['C3', 'C3-parent']  # compression / membership change
    write(r, source)
    f.tick()
    assert calls == [['C3']]


def test_failed_chat_is_retried_alone_without_another_write():
    f, r, source, calls = setup()
    left = {'C1'}
    r.reconcile_sources = lambda sessions: calls.append(list(sessions)) or (left & set(sessions))
    f.watch(WATCHED)
    f.tick()
    calls.clear()
    left.clear()
    f._reconcile_retry_at = 0
    f.tick()
    assert calls == [['C1']]


def test_unscoped_readers_keep_the_conservative_contract():
    f, r, source, calls = setup()
    r.reconcile_scoped = False
    f.watch(['C1', 'C2'])
    f.tick()
    calls.clear()
    write(r, source)
    f.tick()
    assert calls == [['C1', 'C2']]


def test_tick_timing_and_publish_time_are_recorded_and_in_status():
    f, r, source, calls = setup()
    f.watch(['C1'])
    before = time.time()
    f.tick()
    st = f.status()['timing']
    assert st['ticks'] == 1 and st['tick_started_at'] >= before - 1
    assert st['tick_duration_s'] is not None and st['producer_chats'] == 1
    assert st['last_publish_at'] is not None and st['last_publish_rev'] == f.rev


def test_forget_drops_retry_budgets():
    f, r, source, calls = setup()
    f.watch(['C1'])
    f._budgets = {'reconcile': {'C1': 2}, 'hydrate': {'C1': 1, 'C2': 1}}
    with f.lock:
        f._forget('C1')
    assert f._budgets == {'reconcile': {}, 'hydrate': {'C2': 1}}


def load_api():
    path = Path(__file__).resolve().parents[1] / 'dashboard' / 'plugin_api.py'
    api = sys.modules.get('_spf_api_timing_test')
    if api is None:
        try:
            spec = importlib.util.spec_from_file_location('_spf_api_timing_test', path)
            api = importlib.util.module_from_spec(spec)
            sys.modules['_spf_api_timing_test'] = api
            spec.loader.exec_module(api)
        except ImportError:
            sys.modules.pop('_spf_api_timing_test', None)
            pytest.skip('plugin_api needs core on the path')
    return api


def test_timing_file_is_written_atomically(tmp_path):
    api = load_api()
    f, r, source, calls = setup()
    f.watch(['C1'])
    f.tick()
    target = tmp_path / 'timing.json'
    assert api.write_timing(f, target)
    body = json.loads(target.read_text())
    assert body['ticks'] == 1 and body['rev'] == f.rev and body['last_publish_at'] is not None


# ── a scoped producer never reconciles less than the conservative contract did ──────────────────────────────

def test_write_between_source_stamp_and_board_read_is_reconciled():
    """The source stamp is captured before the board read: a write in between is consumed by this pass's
    cursor under the old stamp, and must still reach the producer."""
    f, r, source, calls = setup()
    f.watch(WATCHED)
    f.tick()
    calls.clear()
    state = {'armed': True}

    def stamp():
        seen = source['version']
        if state['armed']:
            state['armed'] = False
            write(r, source, 't_C7', 'C7')
        return seen
    r.reconciliation_stamp = stamp
    f.tick()
    assert calls == [['C7']], 'reconciled by the pass that consumed the event'
    f.tick()
    f.tick()
    assert calls == [['C7']], 'and the stamp catching up reconciles nobody again'


def test_lineage_change_between_source_stamp_and_recompute_is_reconciled():
    """A recompute can adopt the new lineage in a pass whose stamp predates the write: the producer compares
    with the lineage it last reconciled under, not the held bundle's."""
    f, r, source, calls = setup()
    r.panel_stamps = lambda ids: {s: tuple(r.lineages.get(s, [s])) for s in ids}  # membership moves the stamp
    f.watch(WATCHED)
    f.tick()
    calls.clear()
    state = {'armed': True}

    def stamp():
        seen = source['version']
        if state['armed']:
            state['armed'] = False
            r.lineages['C3'] = ['C3', 'C3-parent']
            write(r, source)
        return seen
    r.reconciliation_stamp = stamp
    f.tick()
    assert f.bundles['C3']['bundle']['lineage'] == ['C3', 'C3-parent'], 'presentation already holds the new lineage'
    f.tick()
    assert calls == [['C3']]
    write(r, source)
    f.tick()
    write(r, source)
    f.tick()
    # At most once more: the grown lineage's outcome stamp is first read on the next panel-file move.
    assert calls in ([['C3']], [['C3'], ['C3']]), calls
    settled = list(calls)
    write(r, source)
    f.tick()
    assert calls == settled, 'then settled'


def test_outcome_change_reconciles_its_chat_also_when_it_lands_after_the_source_stamp():
    """A state.db write that moves one chat's outcome/request stamp (no card event, no lineage change)."""
    f, r, source, calls = setup()
    panel = {s: 0 for s in WATCHED}
    r.panel_stamps = lambda ids: {s: panel.get(s, 0) for s in ids}
    f.watch(WATCHED)
    f.tick()
    calls.clear()
    panel['C5'] += 1
    write(r, source)
    f.tick()
    assert calls == [['C5']]
    state = {'armed': True}

    def stamp():
        seen = source['version']
        if state['armed']:
            state['armed'] = False
            panel['C9'] += 1
            write(r, source)
        return seen
    r.reconciliation_stamp = stamp
    f.tick()
    assert calls == [['C5']], 'the stamp this pass holds predates the write'
    f.tick()
    assert calls == [['C5'], ['C9']], 'owed to the pass that sees it'
    write(r, source)
    f.tick()
    assert calls == [['C5'], ['C9']] and not f._producer_owed
    # a request-store write alone is not a source event
    panel['C2'] += 1
    r.stamp += 1
    f.tick()
    assert calls == [['C5'], ['C9']]


def test_deferred_chat_out_of_budget_is_retried_on_a_later_source_write():
    f, r, source, calls = setup()
    left = {'C1'}
    r.reconcile_sources = lambda sessions: calls.append(list(sessions)) or (left & set(sessions))
    f.watch(WATCHED)
    f.tick()
    for _ in range(6):
        f._reconcile_retry_at = 0 if f._reconcile_retry_at is not None else None
        f.tick()
    assert 'C1' in f._reconcile_deferred and f._reconcile_retry_at is None, 'out of retries, still owed'
    calls.clear()
    f.tick()
    assert calls == [], 'no write: nothing retries it'
    write(r, source)  # touches no chat's inputs
    f.tick()
    assert calls == [['C1']], 'any source write re-admits it, alone'
    left.clear()
    write(r, source)
    f.tick()
    assert calls == [['C1'], ['C1']] and not f._reconcile_deferred
    write(r, source)
    f.tick()
    assert calls == [['C1'], ['C1']], 'settled'


def test_a_forgotten_chat_leaves_no_producer_lineage():
    f, r, source, calls = setup()
    f.watch(['C1'])
    f.tick()
    assert f._producer_lineage == {'C1': ('C1',)}
    with f.lock:
        f._forget('C1')
    assert f._producer_lineage == {}


# ── freshness ────────────────────────────────────────────────────────────────────────────────────────────

def test_a_source_write_is_in_the_published_bundle_after_one_pass_and_the_timing_names_it():
    f, r, pushed, t = make()
    source = {'version': 1}
    r.reconcile_scoped = True
    r.reconciliation_stamp = lambda: source['version']
    r.reconcile_sources = lambda sessions: set()
    f.watch(['S1', 'S2'])
    f.tick()
    before = dict(f.timing)
    pushed.clear()
    wrote_at = time.time()
    r.links['S2'][0]['title'] = 'comment landed'
    write(r, source, 't_dddddd4', 'S2')
    f.tick()
    assert [sorted(p['bundles']) for p in pushed if p['bundles']] == [['S2']], 'one pass, that chat only'
    assert pushed[-1]['bundles']['S2']['tasks'][0]['title'] == 'comment landed'
    timing = f.status()['timing']
    assert timing['last_publish_rev'] == f.rev > before['last_publish_rev']
    assert wrote_at <= timing['last_publish_at'] <= time.time()
    last = timing['recent'][-1]
    assert last['rev'] == f.rev and last['sessions'] == ['S2'] and last['at'] == timing['last_publish_at']
    assert wrote_at <= last['pass_started_at'] <= last['at'], 'the pass that read it started after the write'
    assert timing['tick_started_at'] == last['pass_started_at']
    # a push outside a pass (a catch-up) names no pass
    f.changes(['S9'])
    assert f.timing['recent'][-1]['sessions'] == ['S9'] and f.timing['recent'][-1]['pass_started_at'] is None


def test_recent_publishes_are_bounded():
    f, r, source, calls = setup()
    f.watch(['C1'])
    f.tick()
    for i in range(feedmod.RECENT_PUBLISHES + 5):
        r.links['C1'][0]['title'] = f'title {i}'
        write(r, source, 't_C1', 'C1')
        f.tick()
    recent = f.timing['recent']
    assert len(recent) == feedmod.RECENT_PUBLISHES
    assert [e['rev'] for e in recent] == sorted(e['rev'] for e in recent) and recent[-1]['rev'] == f.rev


def test_run_loop_writes_the_timing_file_after_every_pass_and_a_write_is_published_within_a_second(monkeypatch):
    monkeypatch.setenv('HERMES_PANEL_COALESCE', '0')  # legacy subsecond contract
    """Through the real run loop: the token-free timing file shows a source write published in under 1 s."""
    api = load_api()
    f, r, source, calls = setup()
    monkeypatch.setattr(api, 'save_snapshot', lambda feed: True)
    f.watch(WATCHED)
    thread = threading.Thread(target=api._run, args=(f,), daemon=True)
    thread.start()

    def read():
        try:
            return json.loads(api.timing_path().read_text())
        except (OSError, ValueError):
            return {}

    def until(cond, limit=5.0):
        end = time.monotonic() + limit
        while time.monotonic() < end:
            body = read()
            if cond(body):
                return body
            time.sleep(0.005)
        raise AssertionError(read())
    try:
        first = until(lambda b: b.get('ticks', 0) >= 1)
        assert first['watched'] == len(WATCHED)
        for n in range(3):  # back-to-back passes: none may be skipped by the writer
            ticks = read()['ticks']
            r.links['C7'][0]['title'] = f'comment {n}'
            wrote_at = time.time()
            write(r, source, 't_C7', 'C7')
            f._cold.set()
            f._wake.set()
            body = until(lambda b: b.get('ticks', 0) > ticks and b['recent'] and b['recent'][-1]['at'] >= wrote_at)
            last = body['recent'][-1]
            assert last['sessions'] == ['C7'] and last['rev'] == body['last_publish_rev'] == f.rev
            assert 0 <= last['at'] - wrote_at < 1.0, last['at'] - wrote_at
            assert body['producer_chats'] == 1 and body['tick_duration_s'] < 1.0
            assert f.bundles['C7']['bundle']['tasks'][0]['title'] == f'comment {n}'
    finally:
        f.stopped.set()
        f._wake.set()
        thread.join(5)
    assert not thread.is_alive()


# ── real readers ─────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('settle_capture', [False, True])
def test_core_readers_board_write_reconciles_only_the_chat_whose_card_it_touched(tmp_path, monkeypatch, settle_capture):
    """CoreReaders' own `reconcile_scoped`, `reconciliation_stamp`, `board_changes` and `external_capture`
    over a board built by core: writes to one chat's card run the producer for that chat only."""
    kanban_db = pytest.importorskip('hermes_cli.kanban_db')
    from hermes_cli.kanban_db_connect import connect
    from hermes_state import SessionDB
    api = load_api()
    chats = [f'S{i}' for i in range(6)]
    db = SessionDB(db_path=tmp_path / 'state.db')
    for s in chats:
        db.create_session(s, 'desktop')
    path = tmp_path / 'kanban.db'
    conn = connect(path)
    cards = {s: kanban_db.create_task(conn, title=f'Work of {s}', initial_status='running', session_id=s) for s in chats}
    r = api.CoreReaders(state_db=tmp_path / 'state.db')
    assert type(r).reconcile_scoped is True
    monkeypatch.setattr(r, 'boards', lambda: [('default', path)])
    from hermes_cli import kanban_session_activity as ksa
    monkeypatch.setattr(ksa, '_board_db_paths', lambda: [path])  # session_tasks reads this board, not the host's
    calls = []
    monkeypatch.setattr(r, 'reconcile_sources', lambda sessions: calls.append(sorted(sessions)) or set())
    now = {'value': time.time()}
    f = feedmod.Feed(r, emit=lambda payload: None, clock=lambda: now['value'])
    try:
        f.watch(chats)
        f.tick()
        assert calls == [sorted(chats)], 'first sight reconciles every chat'
        assert all(cards[s] in [t['id'] for t in f.bundles[s]['bundle']['tasks']] for s in chats)
        if settle_capture:
            f.tick()
        calls.clear()
        for n in range(3):
            now['value'] += 3  # independent writes outside the production reconcile floor
            kanban_db.add_comment(conn, cards['S3'], 'tester', f'comment {n}')
            f.tick()
            r.release()
        assert calls == [['S3']] * 3, calls
        # a board write to a card no watched chat holds reconciles nobody
        kanban_db.create_task(conn, title='Unwatched', initial_status='running', session_id='elsewhere')
        f.tick()
        assert calls == [['S3']] * 3, calls
        # a state.db write that changes no watched lineage reconciles nobody
        db.create_session('unrelated', 'desktop')
        f.tick()
        assert calls == [['S3']] * 3, calls
        # a lineage change of one chat (real state.db) reconciles that chat
        db._conn.execute("UPDATE sessions SET parent_session_id='unrelated' WHERE id='S4'")
        db._conn.commit()
        f.tick()
        assert calls == [['S3']] * 3 + [['S4']], calls
    finally:
        r.release()
        conn.close()
        db.close()


def test_source_event_ceiling_preserves_time_dependent_producer_work():
    f, r, source, calls = setup()
    now = {'value': 100.0}
    f.clock = lambda: now['value']
    f.watch(['C1', 'C2'])
    f.tick()
    calls.clear()
    now['value'] += 59
    write(r, source, 't_C1', 'C1')
    f.tick()
    assert calls == [['C1']]
    now['value'] += 2
    write(r, source)
    f.tick()
    assert calls == [['C1'], ['C2']], 'only the chat whose canonical fallback is due'
    calls.clear()
    now['value'] += 120
    f.tick()
    assert calls == [], 'wall time alone is never an idle producer sweep'
    write(r, source)
    f.tick()
    assert calls == [['C1', 'C2']], 'the next real source write checks clock/courier work'
    calls.clear()
    write(r, source)
    f.tick()
    assert calls == [], 'not every source write'


@pytest.mark.parametrize('scoped', [True, False])
def test_retiring_remote_answer_finalizes_on_later_unrelated_source_write(tmp_path, monkeypatch, scoped):
    # Real canonical Remote/Mirror/ActionStore, with only transport and feed readers faked.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'user-steps' / 'tests'))
    from test_remote_card_state_machine import setup as remote_setup, age, opens, operation, RETIRING, OWNER
    mirror, store, row, old, op = remote_setup(tmp_path, 'absent', None)
    chat = OWNER['storedSessionId']
    f, r, pushed, clock = make()
    source = {'version': 1}
    calls = []

    def stamp_rows():
        with store._read() as c:
            return tuple(tuple(row) for row in c.execute(
                'SELECT id,revision,state,resolution FROM requests ORDER BY id'))

    def produce(sessions):
        calls.append(list(sessions))
        if chat in sessions:
            mirror.remote.reconcile(OWNER)
        return set()

    r.reconcile_scoped = scoped
    r.reconciliation_stamp = lambda: source['version']
    r.reconcile_sources = produce
    r.panel_stamps = lambda ids: {s: stamp_rows() if s == chat else 0 for s in ids}
    r.links[chat] = [{'id': 't_remote', 'status': 'blocked', 'board': 'default', 'session_id': chat}]
    r.cards['t_remote'] = {'detail': {'task': {'id': 't_remote', 'status': 'blocked', 'session_id': chat},
                                     'links': {'parents': [], 'children': []}, 'link_tasks': [], 'events': []},
                          'board': 'default'}
    f.watch([chat, 'OTHER'])
    f.tick()
    mirror.remote.cards = lambda owner: [dict(row, card_answer_generation='b' * 64)]
    write(r, source, 't_remote', chat)
    f.tick()
    for _ in range(3):
        write(r, source)
        f.tick()
    assert operation(store, op)['retired']['evidence'] == RETIRING
    assert opens(store) == []
    before = stamp_rows()
    age(store, op, 600)
    assert stamp_rows() == before, 'time/courier work moves no request stamp'
    calls.clear()
    for _ in range(4):
        clock['now'] += 30
        write(r, source, 't_unrelated', 'ELSEWHERE')
        f.tick()
    assert any(chat in batch for batch in calls)
    assert operation(store, op)['retired']['evidence'] != RETIRING
    assert len(opens(store)) == 1, 'replacement ask gets a tile'
