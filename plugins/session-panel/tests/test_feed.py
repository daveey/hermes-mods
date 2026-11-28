"""Feed logic against fake readers: what is computed, what is pushed, and the catch-up contract."""
import importlib.util
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location('_spf_feed_test', Path(__file__).resolve().parents[1] / 'dashboard' / 'feed.py')
feedmod = importlib.util.module_from_spec(_spec)
sys.modules['_spf_feed_test'] = feedmod
_spec.loader.exec_module(feedmod)


def detail(cid, status, parents=(), parent_status=None, session='S1', events=()):
    return {'detail': {'task': {'id': cid, 'title': f'T {cid}', 'status': status, 'session_id': session},
                       'links': {'parents': list(parents), 'children': []},
                       'link_tasks': [{'id': p, 'status': (parent_status or {}).get(p, 'todo')} for p in parents],
                       'events': list(events)}, 'board': 'default'}


class Readers:
    def __init__(self):
        self.stamp = 1
        self.lineages = {'S1': ['S1', 'S0'], 'S2': ['S2']}
        self.links = {'S1': [{'id': 't_aaaaaa1', 'status': 'blocked', 'board': 'default', 'session_id': 'S1'}],
                      'S2': [{'id': 't_dddddd4', 'status': 'running', 'board': 'default', 'session_id': 'S2'}]}
        self.finished = {'S1': [{'id': 't_eeeeee5', 'status': 'done'}]}
        self.cards = {
            't_aaaaaa1': detail('t_aaaaaa1', 'blocked', parents=['t_bbbbbb2']),
            't_bbbbbb2': detail('t_bbbbbb2', 'todo', parents=['t_ccccccc3'], session='S9'),
            't_ccccccc3': detail('t_ccccccc3', 'running', session='S9'),
            't_dddddd4': detail('t_dddddd4', 'running', session='S2'),
        }
        self.events = []  # (card, session) changed since the last call
        self.reads = []

    def stamps(self):
        return self.stamp

    def changed_cards(self, cursor):
        out = {c: s for c, s in self.events}
        self.events = []
        return out, (cursor or 0) + 1

    def lineage(self, s):
        return self.lineages.get(s, [s])

    def linked(self, ids):
        return [r for s in ids for r in self.links.get(s, [])]

    def done(self, ids):
        return [r for s in ids for r in self.finished.get(s, [])]

    def card(self, cid, board):
        self.reads.append(cid)
        return self.cards.get(cid)

    def release(self):
        pass


def make(clock=None):
    r = Readers()
    pushed = []
    t = {'now': 1000.0}
    f = feedmod.Feed(r, emit=pushed.append, clock=clock or (lambda: t['now']))
    return f, r, pushed, t


def test_wire_trust_is_stale_after_restore_and_ttl_and_status_reports_ages():
    import json
    f, _, pushed, t = make()
    f.changes(['S1'])
    f._panel_subscribers['window'] = ('S1', t['now'] + 1000)
    def wire(feed):
        return json.loads(feed.changes_bytes(['S1']))['bundles']['S1']['model']['freshness']
    assert wire(f) == {'trusted': True, 'computed_at': 1000, 'ttl_s': 60}
    restored = feedmod.Feed(Readers(), emit=pushed.append, clock=lambda: t['now']).restore(f.snapshot())
    assert wire(restored)['trusted'] is False
    restored._publish(restored._compute_all(['S1']))
    assert wire(restored)['trusted'] is True
    t['now'] += 61
    f._publish({}, heartbeat=True)
    assert wire(f)['trusted'] is False
    assert pushed[-1]['fresh'] == []
    status = f.status()
    assert status['oldest_bundle_age_s'] == 61
    assert status['visible_chats'] == {'S1': {'age_s': 61, 'computed_at': 1000}}


def test_running_evidence_is_recaptured_before_expiry_without_other_events():
    f, r, _, t = make()
    r.links = {'S1': []}
    heartbeat = {'at': t['now'] - 30}
    def linked(ids):
        at = heartbeat['at']
        current = t['now'] < at + 180
        return [{'id': 't_aaaaaa1', 'status': 'running', 'board': 'default',
                 'execution_observations': [{'version': 1, 'state': 'running' if current else 'unknown',
                    'identity': {'kind': 'card', 'work_id': 't_aaaaaa1', 'board': 'default',
                                 'gateway': 'edge', 'profile': 'default', 'run_id': 'run-1'},
                    'owner': {'gateway': 'edge', 'profile': 'default'}, 'generation_portable': True,
                    'source_revision': str(at), 'evidence_refs': ['heartbeat:' + str(at)],
                    'source': 'kanban.card', 'observed_at': at, 'valid_until': at + 180,
                    'freshness': 'current' if current else 'stale'}]}]
    r.linked = lambda ids: linked(ids) if 'S1' in ids else []
    f.changes(['S1', 'S2'])
    quiet_at = f.bundles['S2']['bundle']['computed_at']
    assert f._due(t['now'])[0] == f.full_every_s * .75
    for seconds in range(10, 601, 10):
        t['now'] = 1000 + seconds
        heartbeat['at'] = t['now'] - 30
        # No file/event stamp moves. Only a scoped evidence deadline admits capture.
        f.tick()
        b = f.bundles['S1']['bundle']
        assert b['tasks'][0]['execution_observations'][0]['valid_until'] > t['now']
        assert f._bundle_fresh('S1', t['now'])
        try:
            import hermes_cli.execution_observations  # noqa: F401
        except ImportError:
            pass
        else:
            assert [row['id'] for row in b['model']['sections']['running']] == ['t_aaaaaa1']
    # Stop heartbeats altogether: retain the exact last observation source time.
    for seconds in range(10, 241, 10):
        t['now'] = 1600 + seconds
        f.tick()
    b = f.bundles['S1']['bundle']
    assert b['tasks'][0]['execution_observations'][0]['state'] == 'unknown'
    assert f.bundles['S2']['bundle']['computed_at'] == quiet_at
    assert not b['model']['sections']['running']
    assert f._running_deadlines() == {}, 'silent sources must not create a hot retry loop'
    assert f._due(t['now'])[0] is None


def test_running_deadline_failure_disarms_and_fresh_source_rearms():
    f, r, _, t = make()
    row = {'id': 't_dddddd4', 'status': 'running', 'execution_observations': [
        {'state': 'running', 'freshness': 'current', 'valid_until': 1150}]}
    r.links['S2'] = [row]
    f.changes(['S2'])
    assert f._running_deadlines() == {'S2': 1045}
    good = r.linked
    def fail(ids):
        raise RuntimeError('source unavailable')
    r.linked = fail
    t['now'] = 1045
    f.tick()
    assert f._running_deadlines() == {}
    assert not f._bundle_fresh('S2', t['now'])
    r.linked = good
    r.stamp += 1
    row['execution_observations'][0]['valid_until'] = 1200
    f.tick()
    assert f._running_deadlines() == {'S2': 1090}


def test_a_bundle_holds_the_lineage_cards_done_and_the_whole_chain():
    f, r, _, _ = make()
    b = f.compute('S1')
    assert b['lineage'] == ['S1', 'S0']
    assert [t['id'] for t in b['tasks']] == ['t_aaaaaa1']
    assert [t['id'] for t in b['done']] == ['t_eeeeee5']
    assert set(b['cards']) == {'t_aaaaaa1', 't_bbbbbb2', 't_ccccccc3'}, 'walked parent to parent to the running leaf'


def test_the_walk_follows_card_ids_named_in_waits_and_stops_at_running_or_finished_cards():
    f, r, _, _ = make()
    r.links['S1'] = [{'id': 't_aaaaaa1', 'status': 'scheduled', 'board': 'default', 'waiting_on': 'card t_ffffff6 (running)'}]
    r.cards['t_aaaaaa1'] = detail('t_aaaaaa1', 'scheduled')
    r.cards['t_ffffff6'] = detail('t_ffffff6', 'running', parents=['t_9999999'])
    b = f.compute('S1')
    assert set(b['cards']) == {'t_aaaaaa1', 't_ffffff6'}, 'a running card ends the chain: its parents are not read'


def test_a_finished_parent_is_not_a_hop():
    f, r, _, _ = make()
    r.cards['t_aaaaaa1'] = detail('t_aaaaaa1', 'blocked', parents=['t_bbbbbb2'], parent_status={'t_bbbbbb2': 'done'})
    assert set(f.compute('S1')['cards']) == {'t_aaaaaa1'}


def test_the_walk_is_bounded_and_survives_cycles():
    f, r, _, _ = make()
    r.cards['t_ccccccc3'] = detail('t_ccccccc3', 'blocked', parents=['t_aaaaaa1'])  # a cycle
    b = f.compute('S1')
    assert set(b['cards']) == {'t_aaaaaa1', 't_bbbbbb2', 't_ccccccc3'}
    assert r.reads.count('t_aaaaaa1') == 1


def test_changes_watches_and_returns_everything_on_a_new_epoch_then_only_newer_revisions():
    f, r, _, _ = make()
    first = f.changes(['S1', 'S2'], since=0, epoch=None)
    assert first['reset'] is True and set(first['bundles']) == {'S1', 'S2'}
    again = f.changes(['S1', 'S2'], since=first['rev'], epoch=first['epoch'])
    assert again['reset'] is False and again['bundles'] == {}, 'nothing newer than what the client has'
    stale = f.changes(['S1'], since=0, epoch=first['epoch'])
    assert set(stale['bundles']) == {'S1'}, 'a client behind gets the newer bundle'


def test_concurrent_catch_ups_compute_each_missing_chat_once():
    """Live, hub serve 2026-11-21: 13 request threads each ran `_compute_all` for the same missing chats; the Desktop's
    15 s timeout then retried them, adding more, until no request reached the backend in time (no cards shown)."""
    import threading, time as _t
    f, r, _, _ = make()
    started, release, calls = threading.Event(), threading.Event(), []
    real = r.lineage
    def slow_lineage(s):
        calls.append(s)
        started.set()
        release.wait(5)
        return real(s)
    r.lineage = slow_lineage
    out = {}
    first = threading.Thread(target=lambda: out.__setitem__('a', f.changes(['S1'])))
    first.start()
    assert started.wait(5)
    second = threading.Thread(target=lambda: out.__setitem__('b', f.changes(['S1'])))
    second.start()
    _t.sleep(0.2)
    assert calls == ['S1'], 'the second catch-up waits for the compute already running, it does not start its own'
    release.set()
    first.join(5); second.join(5)
    assert set(out['a']['bundles']) == {'S1'} and set(out['b']['bundles']) == {'S1'}
    assert calls == ['S1']


def test_catch_up_computes_disjoint_work_but_waits_for_every_missing_bundle():
    # Synchronous cold catch-up must not return partial success at an arbitrary wait deadline.
    import threading
    from concurrent.futures import ThreadPoolExecutor
    f, r, _, _ = make()
    started, release, other_done = threading.Event(), threading.Event(), threading.Event()
    real = r.lineage
    def slow_lineage(s):
        if s == 'S1':
            started.set()
            assert release.wait(5)
        else:
            other_done.set()
        return real(s)
    r.lineage = slow_lineage
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(f.changes, ['S1'])
        assert started.wait(3)
        second = pool.submit(f.changes, ['S1', 'S2'])
        try:
            assert other_done.wait(3), 'unrelated work proceeds before joining S1'
            assert not second.done(), 'no empty/partial success while S1 is missing'
        finally:
            release.set()
        assert set(first.result(5)['bundles']) == {'S1'}
        assert set(second.result(5)['bundles']) == {'S1', 'S2'}


def test_a_failed_compute_releases_its_claim():
    f, r, _, _ = make()
    real = r.lineage
    r.lineage = lambda s: (_ for _ in ()).throw(RuntimeError('db locked'))
    assert f.changes(['S1'])['bundles'] == {}
    r.lineage = real
    assert set(f.changes(['S1'])['bundles']) == {'S1'}, 'the next catch-up computes it again'


def test_tick_pushes_only_chats_whose_cards_changed_in_one_batch():
    f, r, pushed, t = make()
    f.changes(['S1', 'S2'])
    assert len(pushed) == 1, 'bundles computed for a catch-up are pushed too'
    pushed.clear()
    f.tick()  # the first tick is a full refresh: nothing differs from what changes() computed
    assert pushed == []
    r.cards['t_ccccccc3'] = detail('t_ccccccc3', 'done', session='S9')  # the leaf of S1's chain finished
    r.events = [('t_ccccccc3', 'S9')]
    r.stamp = 2
    t['now'] += 1
    assert f.tick() == 1
    assert len(pushed) == 1 and set(pushed[0]['bundles']) == {'S1'}, 'S2 references no changed card'
    assert pushed[0]['prev'] + 1 == pushed[0]['rev']
    assert pushed[0]['epoch'] == f.epoch


def test_tick_does_nothing_while_stamps_are_unchanged_and_no_full_refresh_is_due():
    f, r, pushed, t = make()
    f.changes(['S1'])
    f.tick()
    calls = len(r.reads)
    t['now'] += 1
    assert f.tick() == 0
    assert len(r.reads) == calls, 'no reads at all'
    assert all(p.get('heartbeat') or p['bundles'] for p in pushed)


def test_an_unchanged_recompute_is_not_pushed_and_takes_no_revision():
    f, r, pushed, t = make()
    f.changes(['S1'])
    pushed.clear()
    rev = f.rev
    r.events = [('t_aaaaaa1', 'S1')]  # an event (a heartbeat) that changed nothing the panel shows
    r.stamp = 2
    t['now'] += 1
    assert f.tick() == 0 and pushed == [] and f.rev == rev


def test_a_new_card_of_a_watched_chat_marks_it_dirty_through_its_session():
    f, r, pushed, t = make()
    f.changes(['S2'])
    f.tick()
    r.links['S2'].append({'id': 't_7777777', 'status': 'ready', 'board': 'default', 'session_id': 'S2'})
    r.cards['t_7777777'] = detail('t_7777777', 'ready', session='S2')
    r.events = [('t_7777777', 'S2')]
    r.stamp = 3
    t['now'] += 1
    assert f.tick() == 1
    assert [x['id'] for x in pushed[-1]['bundles']['S2']['tasks']] == ['t_dddddd4', 't_7777777']


def test_semantic_source_change_catches_unevented_projection_rebuild():
    from types import SimpleNamespace
    f, r, pushed, t = make()
    f.changes(['S1', 'S2'])
    f.tick()
    r.finished['S2'] = [{'id': 't_8888888', 'status': 'done'}]  # no task event
    r.board_changes = lambda cursor: (SimpleNamespace(affects=lambda held: 'S2' in held['lineage']), cursor)
    r.stamp += 1  # the source commit, NOT a timer, authorizes the semantic capture
    t['now'] += feedmod.FULL_EVERY_S + 1
    assert f.tick() == 1 and set(pushed[-1]['bundles']) == {'S2'}


def test_quiet_chats_stay_held_until_source_change_not_a_quiet_interval():
    f, r, pushed, t = make()
    r.links['S3'] = []
    r.lineages['S3'] = ['S3']
    f.changes(['S2', 'S3'])
    f.tick()  # bootstrap the source cursor
    computed = []
    real = f.compute
    f.compute = lambda s, **kwargs: (computed.append(s), real(s, **kwargs))[1]
    t['now'] += feedmod.FULL_EVERY_S + 1
    f.tick()
    assert computed == [], 'neither open nor quiet chats read sources on a timer'
    t['now'] += feedmod.QUIET_FULL_EVERY_S
    f.tick()
    assert computed == [], 'the old quiet safety sweep is not DB admission'
    r.events = [('t_newcard', 'S3')]  # a card created in S3: recomputed at once
    r.stamp = 77
    t['now'] += 1
    f.tick()
    assert computed == ['S3']


def test_interest_expires_and_is_capped():
    f, r, _, t = make()
    f.changes(['S1'])
    t['now'] += feedmod.INTEREST_TTL_S + 1
    f.watch(['S2'])
    assert set(f.interest) == {'S2'} and 'S1' not in f.bundles
    names = [f'X{i}' for i in range(feedmod.MAX_WATCHED + 10)]
    assert len(f.watch(names)) == feedmod.MAX_REQUEST, 'one request registers at most MAX_REQUEST chats'
    for at in range(0, len(names), feedmod.MAX_REQUEST):
        f.watch(names[at:at + feedmod.MAX_REQUEST])
    assert len(f.interest) == feedmod.MAX_WATCHED


def test_one_failing_chat_does_not_stop_the_rest():
    f, r, pushed, t = make()
    f.changes(['S1', 'S2'])
    f.tick()
    real = r.linked
    r.linked = lambda ids: (_ for _ in ()).throw(RuntimeError('boom')) if 'S1' in ids else real(ids)
    r.cards['t_dddddd4'] = detail('t_dddddd4', 'review', session='S2')
    r.events = [('t_aaaaaa1', 'S1'), ('t_dddddd4', 'S2')]
    r.stamp = 4
    t['now'] += 1
    assert f.tick() == 1 and set(pushed[-1]['bundles']) == {'S2'}
    assert 'S1' in f.bundles, 'S1 keeps its last bundle'


def test_with_terminal_history_matches_user_steps_rules():
    tasks = [{'id': 't_1', 'gateway': 'g', 'profile': 'p', 'board': 'b', 'status': 'running'}]
    history = [
        {'id': 't_2', 'gateway': 'g', 'profile': 'p', 'board': 'b', 'status': 'done', 'unfinished': False, 'session': 'S1',
         'origin_gateway': 'o', 'origin_profile': 'p'},
        {'id': 't_3', 'gateway': 'g', 'profile': 'p', 'board': 'b', 'status': 'done', 'unfinished': None, 'session': 'S1',
         'origin_gateway': 'o', 'origin_profile': 'p'},  # not settled: never history
        {'id': 't_1', 'gateway': 'g', 'profile': 'p', 'board': 'b', 'status': 'done', 'unfinished': False, 'session': 'S1',
         'origin_gateway': 'o', 'origin_profile': 'p'},  # already listed live
    ]
    out = feedmod.with_terminal_history(tasks, history, ['S1'])
    assert [r['id'] for r in out] == ['t_1', 't_2'] and out[1]['source'] == 'origin-history'


def test_every_revision_is_pushed_in_order_so_a_client_following_the_stream_never_has_a_gap():
    f, r, pushed, t = make()
    f.changes(['S1'])               # computed on demand: pushed too
    f.changes(['S2'])
    r.cards['t_dddddd4'] = detail('t_dddddd4', 'review', session='S2')
    r.events = [('t_dddddd4', 'S2')]
    r.stamp = 9
    t['now'] += 1
    f.tick()
    chain = [(p['prev'], p['rev']) for p in pushed]
    assert chain[0][0] == 0 and all(a[1] == b[0] for a, b in zip(chain, chain[1:])), chain
    assert chain[-1][1] == f.rev


def test_a_failing_emit_never_breaks_the_feed():
    r = Readers()
    f = feedmod.Feed(r, emit=lambda p: (_ for _ in ()).throw(RuntimeError('no transport')), clock=lambda: 1000.0)
    out = f.changes(['S1'])
    assert set(out['bundles']) == {'S1'}


def test_with_nothing_to_push_a_heartbeat_goes_out_every_heartbeat_s_and_only_while_watched():
    f, r, pushed, t = make()
    assert f.tick() == 0 and pushed == [], 'nobody watching: silent'
    f.changes(['S1'])
    pushed.clear()
    t['now'] += 1
    f.tick()
    assert pushed == [], 'a push just went out (the catch-up computation)'
    t['now'] += feedmod.HEARTBEAT_S + 1
    f.tick()
    assert len(pushed) == 1 and pushed[0]['heartbeat'] is True and pushed[0]['bundles'] == {}
    assert pushed[0]['prev'] == pushed[0]['rev'] == f.rev, 'a heartbeat continues the chain without a revision'
    t['now'] += 1
    f.tick()
    assert len(pushed) == 1, 'not again before HEARTBEAT_S'


def test_failures_are_counted_shown_and_cleared():
    f, r, pushed, t = make()
    real = r.linked
    r.linked = lambda ids: (_ for _ in ()).throw(RuntimeError('boom')) if 'S1' in ids else real(ids)
    f.changes(['S1'])
    st = f.status()
    assert st['failures']['S1']['count'] == 1 and 'boom' in st['failures']['S1']['error']
    r.linked = real
    t['now'] += feedmod.FULL_EVERY_S + 1
    f.tick()
    assert f.status()['failures'] == {}


def test_concurrent_catch_up_and_tick_keep_the_chain_in_order():
    import threading
    f, r, pushed, t = make()
    f.changes(['S1'])
    sessions = [f'C{i}' for i in range(30)]
    for s in sessions:
        r.lineages[s] = [s]
    def catch_up(s):
        f.changes([s])
    threads = [threading.Thread(target=catch_up, args=(s,)) for s in sessions]
    for th in threads:
        th.start()
    for i in range(5):
        r.stamp += 1
        r.events = [('t_aaaaaa1', 'S1')]
        r.cards['t_aaaaaa1'] = detail('t_aaaaaa1', 'blocked', parents=['t_bbbbbb2'], session=f'S1-{i}')
        t['now'] += 1
        f.tick()
    for th in threads:
        th.join()
    revs = [(p['prev'], p['rev']) for p in pushed if not p.get('heartbeat')]
    assert all(a[1] == b[0] for a, b in zip(revs, revs[1:])), revs
    assert revs[-1][1] == f.rev


def test_a_slower_older_compute_never_replaces_a_newer_bundle():
    import threading
    f, r, pushed, t = make()
    f.watch(['S1'])
    gate, entered = threading.Event(), threading.Event()
    real = r.linked
    state = {'n': 0}

    def linked(ids):
        state['n'] += 1
        if state['n'] == 1:      # the catch-up's compute reads first, then stalls with the old answer
            rows = [dict(x, status='review') for x in real(ids)]
            entered.set()
            gate.wait(5)
            return rows
        return [dict(x, status='blocked') for x in real(ids)]
    r.linked = linked
    th = threading.Thread(target=lambda: f.changes(['S1']))
    th.start()
    entered.wait(5)
    t['now'] += feedmod.FULL_EVERY_S + 1
    # Direct producers still obey the publication fence; tick/changes now deliberately coalesce.
    newer = f.compute('S1')
    f._publish({'S1': newer})      # computes later, publishes 'blocked'
    gate.set()
    th.join()
    assert f.bundles['S1']['bundle']['tasks'][0]['status'] == 'blocked', 'the newer compute wins'
    assert [b['tasks'][0]['status'] for p in pushed for b in p['bundles'].values()] == ['blocked']


def test_every_push_names_the_chats_it_vouches_for():
    f, r, pushed, t = make()
    f.changes(['S1', 'S2'])
    real = r.linked
    r.linked = lambda ids: (_ for _ in ()).throw(RuntimeError('boom')) if 'S2' in ids else real(ids)
    t['now'] += feedmod.FULL_EVERY_S + 1
    f.tick()
    t['now'] += feedmod.HEARTBEAT_S + 1
    f.tick()
    assert pushed[-1].get('heartbeat') and pushed[-1]['fresh'] == ['S1'], 'a failing chat is not vouched for'
    t['now'] += feedmod.INTEREST_TTL_S + 1
    f.watch(['S1'])
    t['now'] += feedmod.HEARTBEAT_S + 1
    f.tick()
    assert 'S2' not in pushed[-1]['fresh'], 'nor a chat no longer watched'


@pytest.mark.parametrize('section', ['tasks', 'done'])
@pytest.mark.parametrize('flag', ['stale', 'partial_source_unknown', '_execution_error'])
def test_source_stale_success_is_not_vouched_and_retries_without_stamp_changes(section, flag):
    f, r, pushed, t = make()
    f.changes(['S1'])
    f.tick()
    rows = r.links['S1'] if section == 'tasks' else r.finished['S1']
    rows[0][flag] = True
    r.stamp += 1
    r.events = [('t_aaaaaa1', 'S1')]
    t['now'] += 1
    f.tick()
    assert not f.failures, 'source-stale fallback succeeds without raising'
    assert f.bundles['S1']['bundle'][section][0][flag] is True
    assert 'S1' not in pushed[-1]['fresh']
    t['now'] += feedmod.HEARTBEAT_S + 1
    f._publish({}, heartbeat=True)
    assert pushed[-1]['heartbeat'] and 'S1' not in pushed[-1]['fresh']
    del rows[0][flag]
    # No new cursor, stamp change, or full refresh: source degradation itself requires retry.
    before = len(r.reads)
    t['now'] += 1
    f.tick()
    assert len(r.reads) > before
    assert flag not in f.bundles['S1']['bundle'][section][0]
    assert 'S1' in pushed[-1]['fresh']
    assert 'S1' not in f._retry


def test_older_source_stale_publication_does_not_dirty_a_newer_fresh_bundle():
    f, r, pushed, t = make()
    f.watch(['S1'])
    r.links['S1'][0]['stale'] = True
    older = f.compute('S1')
    del r.links['S1'][0]['stale']
    newer = f.compute('S1')
    # Fake reader rows alias; copy the old observation as an actual reader would.
    older['tasks'] = [dict(row, stale=True) for row in older['tasks']]
    f._publish({'S1': newer})
    f._publish({'S1': older})
    assert f.bundles['S1']['bundle'] is newer
    assert 'S1' not in f._retry
    t['now'] += feedmod.HEARTBEAT_S + 1
    f._publish({}, heartbeat=True)
    assert pushed[-1]['fresh'] == ['S1']


def test_a_long_recompute_pass_keeps_heartbeating():
    f, r, pushed, t = make()
    f.changes(['S1', 'S2'])
    pushed.clear()
    real = r.linked

    def slow(ids):
        t['now'] += feedmod.HEARTBEAT_S + 1   # each chat takes longer than a heartbeat interval
        return real(ids)
    r.linked = slow
    t['now'] += feedmod.FULL_EVERY_S + 1
    f.tick()
    assert sum(1 for p in pushed if p.get('heartbeat')) >= 1, 'heartbeats went out during the pass'



def test_a_plugin_reload_resumes_the_feed_so_clients_see_no_reset():
    # [daveey 2026-11-21] panels flipped while the hub reloaded plugins for each new session and the feed restarted
    # with a new epoch and no bundles.
    old, r, pushed, t = make()
    first = old.changes(['S1', 'S2'])
    for _ in range(3):  # genuine commits advance the carried sequence numbers
        r.stamp += 1
        r.events = [('t_aaaaaa1', 'S1')]
        t['now'] += 61
        old.tick()
    top = max(b['bundle']['computed_seq'] for b in old.bundles.values())
    assert top > 2
    old.stopped.set()  # plugin unload
    new = feedmod.Feed(r, emit=pushed.append, clock=lambda: t['now']).resume(old)
    assert new.epoch == old.epoch and new.rev == old.rev
    again = new.changes(['S1', 'S2'], since=old.rev, epoch=first['epoch'])
    assert again['reset'] is False and again['bundles'] == {}, 'the client keeps its revision chain'
    # A heartbeat is due while the carried bundles are still young: none is vouched for before a recompute.
    pushed.clear()
    t['now'] += 21
    new._publish({}, heartbeat=True)
    assert pushed and pushed[-1]['fresh'] == [], 'nothing carried is vouched for before a recompute'
    # The recompute's sequence numbers continue past every carried one, so its bundles are stored and pushed.
    r.cards['t_ccccccc3'] = detail('t_ccccccc3', 'done', session='S9')
    pushed.clear()
    new.tick()
    assert all(b['bundle']['computed_seq'] > top for b in new.bundles.values())
    assert pushed and pushed[-1]['epoch'] == first['epoch'] and pushed[-1]['prev'] == old.rev
    assert set(pushed[-1]['bundles']) == {'S1'} and pushed[-1]['fresh'] == ['S1', 'S2']
    # The stopped feed emits nothing more and takes no revision.
    rev = new.rev
    pushed.clear()
    old._publish({'S1': old.compute('S1')}, heartbeat=True)
    assert pushed == [] and new.rev == rev


def test_a_carried_chat_is_not_vouched_for_by_a_heartbeat_in_the_middle_of_its_recompute():
    old, r, pushed, t = make()
    old.changes(['S1', 'S2'])
    old.stopped.set()
    new = feedmod.Feed(r, emit=pushed.append, clock=lambda: t['now']).resume(old)
    t['now'] += 21  # every reservation's heartbeat is due
    pushed.clear()
    # S2 is reserved (its retry mark cleared) and computed; S1's reservation heartbeats before the pass publishes.
    out = new._compute_all(['S2', 'S1'])
    beats = [p for p in pushed if p.get('heartbeat')]
    assert beats and all(p['fresh'] == [] for p in beats), beats
    new._publish(out)  # the same content as carried: no new revision, but the carried mark is cleared
    t['now'] += 21
    new._publish({}, heartbeat=True)
    assert pushed[-1]['heartbeat'] and pushed[-1]['fresh'] == ['S1', 'S2']


def test_a_catch_up_that_reached_the_stopped_feed_lands_in_its_successor():
    old, r, pushed, t = make()
    first = old.changes(['S1'])
    old.stopped.set()
    new = feedmod.Feed(r, emit=pushed.append, clock=lambda: t['now']).resume(old)
    out = old.changes(['S1', 'S2'], since=first['rev'], epoch=first['epoch'])  # a request that held the old feed
    assert 'S2' in new.interest and 'S2' in new.bundles and 'S2' not in old.interest
    assert out['epoch'] == new.epoch and set(out['bundles']) == {'S2'}


def test_a_quiet_carried_chat_is_recomputed_on_the_first_pass_not_after_the_quiet_interval():
    # S3 has no open cards: a full pass alone does not recompute it within QUIET_FULL_EVERY_S.
    old, r, pushed, t = make()
    r.lineages['S3'] = ['S3']
    old.changes(['S3'])
    t['now'] += 61
    old.tick()
    old.stopped.set()
    new = feedmod.Feed(r, emit=pushed.append, clock=lambda: t['now']).resume(old)
    t['now'] += 21
    pushed.clear()
    new.tick()  # unchanged content: no push, but the carried mark is cleared by the recompute
    t['now'] += 21
    new._publish({}, heartbeat=True)
    assert pushed and pushed[-1]['fresh'] == ['S3'], 'the carried quiet chat was recomputed and vouched for'


def test_a_reload_that_lands_while_a_catch_up_computes_hands_the_request_to_the_successor():
    old, r, pushed, t = make()
    first = old.changes(['S1'])
    box = {}
    real = r.lineage

    def lineage(s):  # the reload lands in the middle of the request's compute
        if s == 'S2' and 'new' not in box:
            old.stopped.set()
            box['new'] = feedmod.Feed(r, emit=pushed.append, clock=lambda: t['now']).resume(old)
        return real(s)
    r.lineage = lineage
    out = old.changes(['S1', 'S2'], since=first['rev'], epoch=first['epoch'])
    new = box['new']
    assert 'S2' in new.interest and 'S2' in new.bundles, 'the watch and bundle landed in the live feed'
    assert out['epoch'] == new.epoch and set(out['bundles']) == {'S2'}


def test_forgetting_a_carried_chat_clears_its_carried_mark():
    old, r, pushed, t = make()
    old.changes(['S1'])
    old.stopped.set()
    new = feedmod.Feed(r, emit=pushed.append, clock=lambda: t['now']).resume(old)
    assert 'S1' in new._carried
    new._forget('S1')
    assert 'S1' not in new._carried



class _OlderFeed(feedmod.Feed):
    """Feed code from before resume: publish has no stopped-check and changes knows no successor."""

    def _publish(self, computed, *, heartbeat=False):
        with self.compute_lock:
            self.rev += 1
            self.emit({'epoch': self.epoch, 'rev': self.rev})
            return computed

    def changes(self, sessions, *, since=0, epoch=None):
        self.watch(sessions)
        self._publish({})
        return {'epoch': self.epoch, 'rev': self.rev, 'reset': False, 'bundles': {}}


def test_resuming_an_older_class_feed_routes_its_entry_points_to_the_new_feed():
    r = Readers()
    t = {'now': 1000.0}
    sent = []
    old = _OlderFeed(r, emit=sent.append, clock=lambda: t['now'])
    old.changes(['S1'])
    del old._retry  # the oldest feed.py had none
    new = feedmod.Feed(r, emit=lambda p: None, clock=lambda: t['now']).resume(old)
    sent.clear()
    rev = old.rev
    old._publish({})
    out = old.changes(['S2'])  # a request still holding the old object
    assert sent == [] and old.rev == rev, 'the old object neither pushes nor moves a revision'
    assert out['epoch'] == new.epoch and out['rev'] == new.rev and 'S2' in new.interest
    assert 'S2' in out['bundles'] and 'S2' in new.bundles, 'answered (and computed) by the new feed'


def _roundtrip(snap):
    import json
    return json.loads(json.dumps(snap))


def test_a_restarted_process_restores_the_feed_with_a_revision_gap_and_vouches_only_after_recompute():
    # [daveey 2026-11-21] "we restart the hub for updates, figure out how to preserve the feed".
    old, r, pushed, t = make()
    first = old.changes(['S1', 'S2'])
    for _ in range(2):
        t['now'] += 61
        old.tick()
    top = max(b['bundle']['computed_seq'] for b in old.bundles.values())
    snap = _roundtrip(old.snapshot())
    new = feedmod.Feed(Readers(), emit=pushed.append, clock=lambda: t['now']).restore(snap)
    assert new.epoch == first['epoch'] and new.rev == old.rev + feedmod.RESTORE_REV_GAP
    assert set(new.interest) == {'S1', 'S2'} and set(new.bundles) == {'S1', 'S2'}
    again = new.changes(['S1', 'S2'], since=old.rev, epoch=first['epoch'])
    assert again['reset'] is False and again['bundles'] == {}, 'a client of the old process keeps its chain'
    fresh_client = new.changes(['S1'], since=0, epoch=None)
    assert set(fresh_client['bundles']) == {'S1'}, 'a new client is served the restored bundle at once'
    pushed.clear()
    t['now'] += 21
    new._publish({}, heartbeat=True)
    assert pushed[-1]['fresh'] == [], 'nothing restored is vouched for before this process recomputes it'
    new.tick()
    t['now'] += 21
    new._publish({}, heartbeat=True)
    assert pushed[-1]['fresh'] == ['S1', 'S2']
    assert all(b['bundle']['computed_seq'] > top for b in new.bundles.values())


def test_revisions_after_a_restore_stay_above_what_the_old_process_pushes_during_the_overlap():
    old, r, pushed, t = make()
    first = old.changes(['S1', 'S2'])
    snap = _roundtrip(old.snapshot())
    new = feedmod.Feed(r, emit=pushed.append, clock=lambda: t['now']).restore(snap)
    # The old process keeps running a while after it saved and pushes a change a client then holds.
    r.cards['t_ccccccc3'] = detail('t_ccccccc3', 'done', session='S9')
    r.events, r.stamp = [('t_ccccccc3', 'S9')], 2
    t['now'] += 1
    old.tick()
    client_rev = old.rev
    assert client_rev > first['rev']
    pushed.clear()
    new.tick()  # recomputes every restored chat: S1 differs from what was saved
    assert pushed and all(p['rev'] > client_rev for p in pushed)
    out = new.changes(['S1', 'S2'], since=client_rev, epoch=first['epoch'])
    assert out['reset'] is False, 'the client catches up, never resets'
    assert set(out['bundles']) == {'S1', 'S2'}, 'every restored chat is re-issued, changed or not'


def test_restore_drops_expired_interest_and_refuses_another_shape():
    old, r, pushed, t = make()
    old.changes(['S1'])
    snap = _roundtrip(old.snapshot())
    snap['interest']['S_old'] = t['now'] - 1
    new = feedmod.Feed(r, emit=pushed.append, clock=lambda: t['now']).restore(snap)
    assert 'S_old' not in new.interest and 'S1' in new.interest
    with pytest.raises(ValueError):
        feedmod.Feed(r, emit=pushed.append).restore({**snap, 'version': 99})


def test_a_restored_chat_is_not_vouched_for_by_a_heartbeat_in_the_middle_of_its_recompute():
    old, r, pushed, t = make()
    old.changes(['S1', 'S2'])
    new = feedmod.Feed(r, emit=pushed.append, clock=lambda: t['now']).restore(_roundtrip(old.snapshot()))
    t['now'] += 21
    pushed.clear()
    out = new._compute_all(['S2', 'S1'])  # S2's retry mark clears when it is reserved; S1's reservation heartbeats
    beats = [p for p in pushed if p.get('heartbeat')]
    assert beats and all(p['fresh'] == [] for p in beats), beats


def test_a_quiet_restored_chat_is_recomputed_on_the_first_pass():
    old, r, pushed, t = make()
    r.lineages['S3'] = ['S3']
    old.changes(['S3'])
    t['now'] += 61
    old.tick()
    new = feedmod.Feed(r, emit=pushed.append, clock=lambda: t['now']).restore(_roundtrip(old.snapshot()))
    t['now'] += 21
    new.tick()
    t['now'] += 21
    pushed.clear()
    new._publish({}, heartbeat=True)
    assert pushed and pushed[-1]['fresh'] == ['S3']



def test_a_restored_chat_the_client_holds_newer_is_reissued_even_when_unchanged():
    # The old process saved S1 at h1, then pushed S1=h2 (the client holds it), then S1 went back to h1. The new
    # process recomputes h1 = what it restored: without a new revision the client would keep h2 while vouched.
    old, r, pushed, t = make()
    first = old.changes(['S1'])
    snap = _roundtrip(old.snapshot())
    r.cards['t_ccccccc3'] = detail('t_ccccccc3', 'done', session='S9')
    r.events, r.stamp = [('t_ccccccc3', 'S9')], 2
    t['now'] += 1
    old.tick()
    client_rev = old.rev
    r.cards['t_ccccccc3'] = detail('t_ccccccc3', 'running', session='S9')  # back to what was saved
    new = feedmod.Feed(r, emit=pushed.append, clock=lambda: t['now']).restore(snap)
    pushed.clear()
    new.tick()
    sent = [p for p in pushed if p.get('bundles')]
    assert sent and 'S1' in sent[-1]['bundles'] and sent[-1]['rev'] > client_rev
    out = new.changes(['S1'], since=client_rev, epoch=first['epoch'])
    assert set(out['bundles']) == {'S1'} and out['bundles']['S1']['cards']['t_ccccccc3']['detail']['task']['status'] == 'running'


def test_restore_keeps_only_bundles_of_chats_still_watched():
    old, r, pushed, t = make()
    old.changes(['S1', 'S2'])
    snap = _roundtrip(old.snapshot())
    snap['interest']['S2'] = t['now'] - 1  # nobody re-registered it
    new = feedmod.Feed(r, emit=pushed.append, clock=lambda: t['now']).restore(snap)
    assert set(new.bundles) == {'S1'} and 'S2' not in new._retry and 'S2' not in new._carried


def test_a_plugin_reload_right_after_a_restore_still_reissues_the_restored_chats():
    old, r, pushed, t = make()
    first = old.changes(['S1'])
    snap = _roundtrip(old.snapshot())
    r.cards['t_ccccccc3'] = detail('t_ccccccc3', 'done', session='S9')
    r.events, r.stamp = [('t_ccccccc3', 'S9')], 2
    t['now'] += 1
    old.tick()
    client_rev = old.rev
    r.cards['t_ccccccc3'] = detail('t_ccccccc3', 'running', session='S9')
    restored = feedmod.Feed(r, emit=pushed.append, clock=lambda: t['now']).restore(snap)
    restored.stopped.set()  # a plugin reload before its first recompute
    new = feedmod.Feed(r, emit=pushed.append, clock=lambda: t['now']).resume(restored)
    pushed.clear()
    new.tick()
    out = new.changes(['S1'], since=client_rev, epoch=first['epoch'])
    assert set(out['bundles']) == {'S1'}, 'still re-issued after the reload'
