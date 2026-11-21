"""Feed logic against fake readers: what is computed, what is pushed, and the catch-up contract."""
import importlib.util
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location('_spf_feed_test', Path(__file__).resolve().parents[1] / 'feed.py')
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


def make(clock=None):
    r = Readers()
    pushed = []
    t = {'now': 1000.0}
    f = feedmod.Feed(r, emit=pushed.append, clock=clock or (lambda: t['now']))
    return f, r, pushed, t


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


def test_full_refresh_recomputes_every_watched_chat_and_catches_unevented_changes():
    f, r, pushed, t = make()
    f.changes(['S1', 'S2'])
    f.tick()
    r.finished['S2'] = [{'id': 't_8888888', 'status': 'done'}]  # no event seen (a projection rebuild)
    t['now'] += feedmod.FULL_EVERY_S + 1
    assert f.tick() == 1 and set(pushed[-1]['bundles']) == {'S2'}


def test_interest_expires_and_is_capped():
    f, r, _, t = make()
    f.changes(['S1'])
    t['now'] += feedmod.INTEREST_TTL_S + 1
    f.watch(['S2'])
    assert set(f.interest) == {'S2'} and 'S1' not in f.bundles
    f.watch([f'X{i}' for i in range(feedmod.MAX_WATCHED + 10)])
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
