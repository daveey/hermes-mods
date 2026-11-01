"""Canonical asks retire when core's ``human_request.resolved_check`` resolved exactly that card ask.

Core (kanban_ask_resolved) records a probe that exited 0 as ``ask_resolved`` {cmd, rc, output} followed, in the
same transaction, by ``unblocked`` {via: ask_resolved}. Only then, and only for a request that restates the
card's pending human ask (not one that merely names the card), is the request withdrawn (by=system): never
approved, never dispatched. Every other unblock is still a retry that keeps the ask. Board rows here mirror
core's event shapes; ``test_kanban_ask_resolved_check.py`` drives the real core producer.
"""
import json
import sqlite3

import pytest

from test_withdrawal import OWNER, T0, ROOT, board, complete_request, core, decision, env, load_module, router_client

CARD = 't_0a0a0a0a'
CMD = 'test -e /tmp/paired-flag'
ACTION = 'Press the pair button on the watch. Unblock = finish pairing; archive = drop the watch.'
TITLE = f'Press the pair button on the watch ({CARD})'


def hr(cmd: 'str | None' = CMD, action=ACTION):
    return {'action': action, 'why_human': 'physical button', 'interaction': 'physical', 'scope': 'watch',
            'evidence': 'hub shows it unpaired', **({'resolved_check': cmd} if cmd else {})}


def blocked(ts, request=None, **extra):
    return ('blocked', int(ts), json.dumps({'kind': 'needs_input', 'reason': 'needs David',
                                           'human_request': request or hr(), **extra}))


def resolved(ts, cmd=CMD, rc: object = 0):
    return ('ask_resolved', int(ts), json.dumps({'cmd': cmd, 'rc': rc, 'output': 'paired'}))


def unblocked(ts, via: 'str | None' = 'ask_resolved'):
    return ('unblocked', int(ts), json.dumps({'via': via}) if via else None)


def resolve_card(tmp_path, ts=T0 + 100, status='ready', cmd=CMD, card=CARD):
    return board(tmp_path, {'id': card, 'status': status, 'events': [resolved(ts, cmd), unblocked(ts)]})


def withdrawn_ids(store, ledger, boards, now):
    return [x['requestId'] for x in core.reconcile_moot(store, ledger, OWNER, boards=boards, now=now)]


def test_request_restating_the_card_ask_is_withdrawn_once_core_resolves_it(tmp_path):
    store, ledger = env(tmp_path)
    boards = board(tmp_path, {'id': CARD, 'status': 'blocked', 'events': [blocked(T0 - 10)]})
    r = store.create(OWNER, complete_request(title=TITLE), created_at=T0)
    assert withdrawn_ids(store, ledger, boards, T0 + 50) == []  # still blocked: the ask stands
    resolve_card(tmp_path)
    assert withdrawn_ids(store, ledger, boards, T0 + 110) == [r['requestId']]
    view = store.view(OWNER, r['requestId'])
    assert view['state'] == 'withdrawn' and 'resolution' not in view  # withdrawn, not decided
    assert view['withdrawal']['by'] == 'system'
    assert CARD in view['withdrawal']['reason'] and 'resolved outside Hermes' in view['withdrawal']['reason']
    assert store.undelivered(OWNER['gatewayId'], OWNER['profile']) == []  # no operation, nothing dispatched
    assert withdrawn_ids(store, ledger, boards, T0 + 120) == []  # idempotent


def test_explicit_kanban_source_ref_with_a_restated_ask_is_withdrawn(tmp_path):
    store, ledger = env(tmp_path)
    ref = dict(gatewayId=OWNER['gatewayId'], profile=OWNER['profile'], type='kanban', board='default', id=CARD)
    boards = board(tmp_path, {'id': CARD, 'status': 'blocked', 'events': [blocked(T0 - 10)]})
    r = store.create(OWNER, complete_request(title='Press the pair button on the watch', refs=[ref]), created_at=T0)
    resolve_card(tmp_path)
    assert withdrawn_ids(store, ledger, boards, T0 + 110) == [r['requestId']]


def test_extracted_step_leaves_public_open_steps(tmp_path, monkeypatch):
    # The For-you queue and core.open_steps (session-outcome) both drop the ask, ledger mirror included.
    (tmp_path / 'user-steps').mkdir()
    store = core.ActionStore(tmp_path / 'user-steps' / 'actions.sqlite3')
    ledger = core.Ledger(tmp_path / 'ledger', tmp_path / 'missing-state.db')
    monkeypatch.setattr(core, 'home', lambda: tmp_path)
    monkeypatch.setattr(core, 'server_owner', lambda sid: dict(OWNER, storedSessionId=sid))
    boards = board(tmp_path, {'id': CARD, 'status': 'blocked', 'events': [blocked(T0 - 10)]})
    span = f'Please press the pair button on the watch so {CARD} can finish pairing.'
    step = {'text': f'Press the pair button on the watch ({CARD})', 'kind': 'do', 'verbatim_span': span,
            'options': []}
    out = core.process_reply(span, 'session-a', ledger=ledger, action_store=store, action_owner=OWNER, now=T0,
                             llm=lambda *a: json.dumps({'steps': [step], 'resolved': []}))
    rid = out['steps'][0]['requestId']
    assert [s.get('requestId') for s in core.open_steps('session-a', ledger)] == [rid]
    resolve_card(tmp_path)
    assert withdrawn_ids(store, ledger, boards, T0 + 110) == [rid]
    assert core.open_steps('session-a', ledger) == []
    assert store.views(OWNER) == []


def test_for_you_read_retires_the_resolved_ask_without_a_decision(tmp_path):
    store, ledger = env(tmp_path)
    boards = board(tmp_path, {'id': CARD, 'status': 'blocked', 'events': [blocked(T0 - 10)]})
    r = store.create(OWNER, complete_request(title=TITLE), created_at=T0)
    with router_client(tmp_path, store, boards) as client:
        assert [v['requestId'] for v in client.get('/requests', params={'session': 'session-a'}).json()['requests']] == [r['requestId']]
        resolve_card(tmp_path)
        response = client.get('/requests', params={'session': 'session-a'})
        assert response.status_code == 200
        assert response.json()['requests'] == []
    assert store.view(OWNER, r['requestId'])['state'] == 'withdrawn'
    assert store.undelivered(OWNER['gatewayId'], OWNER['profile']) == []


@pytest.mark.parametrize('via', ['ask_resolved', None])
def test_legacy_open_asks_drops_a_normally_unblocked_card(tmp_path, monkeypatch, via):
    # Legacy open-asks watches current status, unlike canonical decisions. No plugin change is needed.
    plugin = load_module('retirement_open_asks', ROOT.parent / 'open-asks' / '__init__.py')
    boards = board(tmp_path, {'id': CARD, 'status': 'blocked', 'events': [blocked(T0 - 10)]})
    state = plugin.core.State(tmp_path / 'open-asks.json')
    state.add_card('session-a', CARD, [ACTION])
    monkeypatch.setattr(plugin, '_eligible', lambda *a: True)
    monkeypatch.setattr(plugin, '_state', lambda: state)
    monkeypatch.setattr(plugin, '_boards', lambda: [path for _, path in boards])
    assert CARD in plugin.pre_llm_call(session_id='session-a')['context']
    board(tmp_path, {'id': CARD, 'status': 'ready', 'events': [unblocked(T0 + 100, via)]})
    assert plugin.pre_llm_call(session_id='session-a') is None
    assert state.cards('session-a') == []
    assert plugin.core.blocked_ids([path for _, path in boards], [CARD]) == set()


def test_a_request_that_only_names_the_card_is_context_not_identity(tmp_path):
    store, ledger = env(tmp_path)
    boards = board(tmp_path, {'id': CARD, 'status': 'blocked', 'events': [blocked(T0 - 10)]})
    r = store.create(OWNER, complete_request(title=f'Approve deploying the firmware build for {CARD}'), created_at=T0)
    resolve_card(tmp_path)
    assert withdrawn_ids(store, ledger, boards, T0 + 110) == []
    assert store.get(OWNER, r['requestId'])['state'] == 'open'


@pytest.mark.parametrize('events', [
    [unblocked(T0 + 100, via=None)],                                  # David's / an agent's retry unblock
    [unblocked(T0 + 100, via='dashboard')],
    [resolved(T0 + 100, rc=False), unblocked(T0 + 100)],
    [resolved(T0 + 100, rc='0'), unblocked(T0 + 100)],
    [resolved(T0 + 100, rc=1), unblocked(T0 + 100)],                  # not an exit 0
    [resolved(T0 + 100, cmd='true'), unblocked(T0 + 100)],            # a different probe resolved
    [resolved(T0 + 100)],                                             # evidence without the unblock
    [unblocked(T0 + 100), resolved(T0 + 101)],                        # wrong order: another unblock came first
], ids=['plain-unblock', 'other-via', 'bool-rc', 'string-rc', 'rc1', 'other-cmd', 'no-unblock', 'unblocked-first'])
def test_only_the_exact_core_resolution_counts(tmp_path, events):
    store, ledger = env(tmp_path)
    boards = board(tmp_path, {'id': CARD, 'status': 'blocked', 'events': [blocked(T0 - 10)]})
    r = store.create(OWNER, complete_request(title=TITLE), created_at=T0)
    board(tmp_path, {'id': CARD, 'status': 'ready', 'events': events})
    assert withdrawn_ids(store, ledger, boards, T0 + 110) == []
    assert store.get(OWNER, r['requestId'])['state'] == 'open'


def test_an_ask_amended_after_the_request_resolves_a_different_ask(tmp_path):
    store, ledger = env(tmp_path)
    boards = board(tmp_path, {'id': CARD, 'status': 'blocked', 'events': [blocked(T0 - 10)]})
    r = store.create(OWNER, complete_request(title=TITLE), created_at=T0)
    board(tmp_path, {'id': CARD, 'status': 'blocked', 'events': [
        blocked(T0 + 30, hr(), classified_in_place=True, metadata_only=True)]})
    resolve_card(tmp_path)
    assert withdrawn_ids(store, ledger, boards, T0 + 110) == []
    assert store.get(OWNER, r['requestId'])['state'] == 'open'


def test_a_pending_ask_without_resolved_check_is_never_matched(tmp_path):
    store, ledger = env(tmp_path)
    boards = board(tmp_path, {'id': CARD, 'status': 'blocked', 'events': [blocked(T0 - 10, hr(cmd=None))]})
    store.create(OWNER, complete_request(title=TITLE), created_at=T0)
    resolve_card(tmp_path)
    assert withdrawn_ids(store, ledger, boards, T0 + 110) == []


@pytest.mark.parametrize('later,status', [
    ([blocked(T0 + 200)], 'blocked'),                                         # re-blocked on David again
    ([('claimed', T0 + 150, '{}'), ('review_requested', T0 + 200)], 'review'),
    ([('gave_up', T0 + 200)], 'triage'),
], ids=['reblock', 'review', 'triage'])
def test_a_card_that_waits_again_after_the_resolution_keeps_the_ask(tmp_path, later, status):
    store, ledger = env(tmp_path)
    boards = board(tmp_path, {'id': CARD, 'status': 'blocked', 'events': [blocked(T0 - 10)]})
    r = store.create(OWNER, complete_request(title=TITLE), created_at=T0)
    resolve_card(tmp_path)
    board(tmp_path, {'id': CARD, 'status': status, 'events': later})
    assert withdrawn_ids(store, ledger, boards, T0 + 210) == []
    assert store.get(OWNER, r['requestId'])['state'] == 'open'


def test_a_revision_asked_after_the_resolution_is_stale(tmp_path):
    # The current revision was asked (an older producer process re-asked it: no lifecycle stamp, so it is a fresh
    # ask as of the pass) after the card had already resumed: that revision was never answered by the probe.
    store, ledger = env(tmp_path)
    boards = board(tmp_path, {'id': CARD, 'status': 'blocked', 'events': [blocked(T0 - 10)]})
    r = store.create(OWNER, complete_request(title=TITLE), created_at=T0)
    resolve_card(tmp_path)
    with sqlite3.connect(tmp_path / 'actions.sqlite3') as c:
        ns, body = c.execute('SELECT namespace, body FROM revisions WHERE id=?', (r['requestId'],)).fetchone()
        c.execute('INSERT INTO revisions VALUES (?,?,?,?)',
                  (ns, r['requestId'], 2, json.dumps(dict(json.loads(body), revision=2))))
        c.execute("UPDATE requests SET revision=2 WHERE id=?", (r['requestId'],))
    assert withdrawn_ids(store, ledger, boards, T0 + 200) == []
    assert store.created_at(OWNER, r['requestId']) == T0 + 200
    assert store.get(OWNER, r['requestId'])['state'] == 'open'


def test_asked_before_the_card_blocked_is_not_counted(tmp_path):
    store, ledger = env(tmp_path)
    boards = board(tmp_path, {'id': CARD, 'status': 'running', 'events': [('claimed', T0 - 50, '{}')]})
    store.create(OWNER, complete_request(title=TITLE), created_at=T0)
    board(tmp_path, {'id': CARD, 'status': 'blocked', 'events': [blocked(T0 + 20)]})
    resolve_card(tmp_path)
    assert withdrawn_ids(store, ledger, boards, T0 + 110) == []


def test_every_named_card_must_settle(tmp_path):
    other = 't_0b0b0b0b'
    store, ledger = env(tmp_path)
    boards = board(tmp_path, {'id': CARD, 'status': 'blocked', 'events': [blocked(T0 - 10)]},
                   {'id': other, 'status': 'blocked', 'events': [blocked(T0 - 10, hr(cmd=None, action='Approve it'))]})
    r = store.create(OWNER, complete_request(title=f'{TITLE} and approve {other}'), created_at=T0)
    resolve_card(tmp_path)
    assert withdrawn_ids(store, ledger, boards, T0 + 110) == []
    board(tmp_path, {'id': other, 'status': 'done', 'completed_at': int(T0 + 150), 'events': [('completed', T0 + 150)]})
    assert withdrawn_ids(store, ledger, boards, T0 + 160) == [r['requestId']]


def test_ambiguous_card_and_foreign_owner_refs_never_retire(tmp_path):
    store, ledger = env(tmp_path)
    boards = board(tmp_path, {'id': CARD, 'status': 'blocked', 'events': [blocked(T0 - 10)]})
    r = store.create(OWNER, complete_request(title=TITLE), created_at=T0)
    foreign = dict(gatewayId='other-gateway', profile='default', type='kanban', board='default', id='t_0c0c0c0c')
    f = store.create(OWNER, complete_request(title='Press the pair button on the watch', refs=[foreign]), created_at=T0)
    resolve_card(tmp_path)
    resolve_card(tmp_path, card='t_0c0c0c0c')
    twin = tmp_path / 'twin'
    twin.mkdir()
    dup = board(twin, {'id': CARD, 'status': 'ready', 'events': []})
    assert withdrawn_ids(store, ledger, boards + [('twin', dup[0][1])], T0 + 110) == []
    assert store.get(OWNER, r['requestId'])['state'] == 'open'
    assert withdrawn_ids(store, ledger, boards, T0 + 110) == [r['requestId']]
    assert store.get(OWNER, f['requestId'])['state'] == 'open'  # another owner's card ref is not watched


def test_a_recorded_decision_wins_over_the_resolution(tmp_path):
    store, ledger = env(tmp_path)
    boards = board(tmp_path, {'id': CARD, 'status': 'blocked', 'events': [blocked(T0 - 10)]})
    r = store.create(OWNER, complete_request(title=TITLE), created_at=T0)
    store.decide(OWNER, decision(r))
    resolve_card(tmp_path)
    assert withdrawn_ids(store, ledger, boards, T0 + 110) == []
    assert store.get(OWNER, r['requestId'])['state'] != 'withdrawn'
