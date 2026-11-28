"""Real ActionStore -> remote mirror -> M1 courier journals -> owner transaction.

Authenticated service/OS identity are offline fixtures, not a live HTTPS proof.
Run with run-core-integration.sh and HERMES_HUB_CLIENT_REAL pointing to M1+ client.
"""
import copy
import hashlib
import json
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_withdrawal import ROOT, OWNER, core, load_module
from test_kanban_mirror import fixture, decide


def remote_row(hr, **changes):
    return dict(gateway='remote', profile='worker', board='other', id='t_remote',
                session=OWNER['storedSessionId'], status='blocked', human_request=hr,
                card_answer_generation='a' * 64, title='Choose remotely', **changes)


def native_target(**changes):
    return dict(ownerGateway='remote', ownerProfile='worker', board='other', task='t_remote', **changes)


def test_card_ask_tool_returns_native_bound_request_without_ad_hoc_publication(tmp_path, monkeypatch):
    from test_review_regressions import plugin_at
    plugin = plugin_at(tmp_path, monkeypatch)
    mirror, store, _, hr = fixture(tmp_path)
    mirror.remote.cards = lambda owner: [remote_row(hr)]
    monkeypatch.setattr(plugin, '_presenter', lambda st: mirror)
    out = json.loads(plugin.publish_action_request({'cardAsk': native_target()}, session_id=OWNER['storedSessionId']))
    assert 'error' not in out
    request = out['request']
    assert request['requestId'] == mirror.remote.request_for(OWNER, native_target())['requestId']
    assert len(store.requests(OWNER)) == 1
    assert mirror.binding(OWNER, request['requestId'])
    op = decide(store, request)
    envelope = mirror.remote.envelope(op, mirror._bound(op))
    assert envelope['args']['answer']['choice'] == 'green'
    assert envelope['args']['answer']['generation'] == 'a' * 64
    assert 'does not resume' in plugin.PRODUCER_SCHEMA['description']


@pytest.mark.parametrize('change', [dict(stale=True), dict(card_answer_generation=None), dict(status='done')])
def test_card_ask_lookup_refuses_unknown_or_closed_target(tmp_path, change):
    mirror, store, _, hr = fixture(tmp_path)
    row = remote_row(hr)
    row.update(change)
    mirror.remote.cards = lambda owner: [row]
    with pytest.raises(core.ActionError, match='unavailable'):
        mirror.remote.request_for(OWNER, native_target())
    assert not store.requests(OWNER)


@pytest.mark.parametrize('field', ['ownerGateway', 'ownerProfile', 'board', 'task'])
def test_card_ask_identity_cannot_select_a_twin(tmp_path, field):
    mirror, store, _, hr = fixture(tmp_path)
    mirror.remote.cards = lambda owner: [remote_row(hr)]
    target = native_target()
    target[field] = 'other-target'
    with pytest.raises(core.ActionError, match='unavailable'):
        mirror.remote.request_for(OWNER, target)
    assert len(store.requests(OWNER)) == 1  # only the real linked ask was published


@pytest.mark.parametrize('extra', ['generation', 'choice', 'operationId', 'sourceRefs'])
def test_card_ask_never_accepts_caller_binding_or_decision(tmp_path, extra):
    mirror, store, _, _ = fixture(tmp_path)
    with pytest.raises(core.ActionError):
        mirror.remote.request_for(OWNER, dict(native_target(), **{extra: 'forged'}))
    assert not store.requests(OWNER)


def test_card_ask_does_not_rebind_recorded_request(tmp_path):
    mirror, store, _, hr = fixture(tmp_path)
    mirror.remote.cards = lambda owner: [remote_row(hr)]
    request = mirror.remote.request_for(OWNER, native_target())
    decide(store, request)
    with pytest.raises(core.ActionError, match='undecided'):
        mirror.remote.request_for(OWNER, native_target())
    assert len(store.requests(OWNER)) == 1


def test_card_ask_does_not_reuse_withdrawn_unresolved_request(tmp_path):
    mirror, store, _, hr = fixture(tmp_path)
    mirror.remote.cards = lambda owner: [remote_row(hr)]
    request = mirror.remote.request_for(OWNER, native_target())
    store.withdraw(OWNER, request['requestId'], by='agent', reason='No longer needed')
    withdrawn = store.get(OWNER, request['requestId'])
    assert withdrawn['state'] == 'withdrawn' and not withdrawn.get('resolution')
    with pytest.raises(core.ActionError, match='undecided'):
        mirror.remote.request_for(OWNER, native_target())
    assert store.requests(OWNER, include_withdrawn=True) == [withdrawn]  # no substitute ask or decision


def test_card_ask_does_not_reuse_explicit_chat_revision(tmp_path):
    mirror, store, _, hr = fixture(tmp_path)
    mirror.remote.cards = lambda owner: [remote_row(hr)]
    request = mirror.remote.request_for(OWNER, native_target())
    replacement = dict(request, title='Explicit replacement scope', kind='approval',
                       choices=[dict(id='approve', label='Approve expanded scope', effect='approve')],
                       outcomes=dict(approve='Deliver approval to the owning chat', revise='Prepare a replacement'))
    revised = store.revise(OWNER, request['requestId'], 1, replacement)
    with pytest.raises(core.ActionError, match='undecided'):
        mirror.remote.request_for(OWNER, native_target())
    assert store.requests(OWNER) == [revised]


def test_card_ask_does_not_reuse_same_kind_revision(tmp_path):
    mirror, store, _, hr = fixture(tmp_path)
    mirror.remote.cards = lambda owner: [remote_row(hr)]
    request = mirror.remote.request_for(OWNER, native_target())
    replacement = dict(request, title='Agent-edited copy',
                       choices=[dict(request['choices'][0], label='Something else')])
    revised = store.revise(OWNER, request['requestId'], 1, replacement)
    with pytest.raises(core.ActionError, match='undecided'):
        mirror.remote.request_for(OWNER, native_target())
    assert store.requests(OWNER) == [revised]


def test_card_ask_refuses_generation_changed_between_membership_reads(tmp_path):
    mirror, store, _, hr = fixture(tmp_path)
    reads = []
    def cards(owner):
        row = remote_row(hr)
        row['card_answer_generation'] = ('a' if not reads else 'b') * 64
        reads.append(True)
        return [row]
    mirror.remote.cards = cards
    with pytest.raises(core.ActionError, match='undecided'):
        mirror.remote.request_for(OWNER, native_target())
    assert len(reads) == 2
    assert len(store.requests(OWNER)) == 1
    assert store.requests(OWNER)[0]['state'] == 'open'


@pytest.mark.parametrize('field', ['ownerGateway', 'ownerProfile', 'board', 'task'])
@pytest.mark.parametrize('value', ['', None, 12, '../other', 'with space'])
def test_card_ask_identity_is_strict(tmp_path, field, value):
    mirror, store, _, _ = fixture(tmp_path)
    target = native_target()
    target[field] = value
    with pytest.raises(core.ActionError, match='Invalid remote card identity'):
        mirror.remote.request_for(OWNER, target)
    assert not store.requests(OWNER)


@pytest.mark.parametrize('extra', ['request', 'receipt', 'withdraw', 'decisionCard'])
def test_card_ask_tool_cannot_mix_publication_paths(tmp_path, monkeypatch, extra):
    from test_review_regressions import plugin_at
    plugin = plugin_at(tmp_path, monkeypatch)
    mirror, store, _, hr = fixture(tmp_path)
    mirror.remote.cards = lambda owner: [remote_row(hr)]
    monkeypatch.setattr(plugin, '_presenter', lambda st: mirror)
    out = json.loads(plugin.publish_action_request({'cardAsk': native_target(), extra: {}},
                                                   session_id=OWNER['storedSessionId']))
    assert 'error' in out
    assert not store.requests(OWNER)


@pytest.mark.parametrize('failure', ['missing-client', 'gateway', 'key-exit', 'offline', 'http'])
def test_real_courier_refusals_and_unavailability(tmp_path, failure):
    module = load_module('courier_refusals', ROOT / 'remote_cards.py')
    queued, closed = [], []
    def read_key():
        if failure == 'key-exit':
            raise SystemExit('No key')
        return 'fixture-key'
    def call(*args):
        if failure == 'offline':
            raise RuntimeError('offline')
        return (503, {})
    client = SimpleNamespace(open_db=lambda path: SimpleNamespace(commit=lambda: None,
        close=lambda: closed.append(True)), mget=lambda db, key: 'wrong' if failure == 'gateway' else 'fixture-sync',
        queue_request=lambda *args, **kwargs: queued.append((args, kwargs)),
        load_config=lambda: {'hub_url': 'https://example.test'}, read_key=read_key,
        Api=lambda *args: SimpleNamespace(call=call))
    exchange = module.Courier.__new__(module.Courier)
    exchange.view = SimpleNamespace(load_client=lambda: None if failure == 'missing-client' else client,
        db_path=lambda: tmp_path / 'courier.db', _send_now=lambda *args: None, SEND_WAIT_S=0)
    envelope = dict(to_gw='remote', key='worker/other/t_remote', args={'source': {'gateway': 'fixture-sync'}})
    with pytest.raises(OSError):
        exchange.exchange(envelope, 'exact-request')
    assert len(queued) == (0 if failure in ('missing-client', 'gateway') else 1)
    assert len(closed) == (0 if failure == 'missing-client' else 1)


@pytest.mark.parametrize('field', ['id', 'from', 'to_gw', 'coll', 'key', 'action', 'args',
    'state', 'result', 'receipt-choice', 'receipt-operationId', 'receipt-originGateway',
    'receipt-destination', 'receipt-executionStarted', 'receipt-decisionProvenance'])
def test_transport_record_and_receipt_are_independently_validated(tmp_path, field):
    mirror, store, _, hr = fixture(tmp_path)
    mirror.remote.cards = lambda owner: [remote_row(hr)]
    mirror.remote.reconcile(OWNER)
    op = decide(store, store.requests(OWNER)[0])
    calls = []
    class Transport:
        def exchange(self, envelope, rid):
            calls.append(rid)
            expected = 'hr-card-answer-' + hashlib.sha256(json.dumps(
                [op['owner'], op['operationId']], sort_keys=True).encode()).hexdigest()
            assert rid == expected
            receipt = dict(copy.deepcopy(envelope['args']['answer']), originGateway='fixture-sync',
                destination='kanban_ask', executionStarted=False, decisionProvenance='recorded_desktop_operation')
            record = dict(copy.deepcopy(envelope), id=rid, state='done', result=receipt,
                          **{'from': 'gw:fixture-sync'})
            if field.startswith('receipt-'):
                receipt[field.removeprefix('receipt-')] = 'tampered'
            else:
                record[field] = 'tampered'
            return record
    mirror.remote.courier = Transport()
    with pytest.raises((OSError, core.ActionError)):
        mirror.deliver(op)
    assert len(calls) == 1


@pytest.mark.parametrize('matching', [False, True])
def test_remote_readback_requires_equal_fresh_receipt(tmp_path, matching):
    mirror, store, _, hr = fixture(tmp_path)
    mirror.remote.cards = lambda owner: [remote_row(hr)]
    mirror.remote.reconcile(OWNER)
    op = decide(store, store.requests(OWNER)[0])
    calls = []
    mirror.remote.deliver = lambda *args: calls.append(args) or {'fresh': 'receipt'}
    receipt = {'fresh': 'receipt'} if matching else {'stale': 'receipt'}
    assert mirror.readback(op, receipt) is matching
    assert len(calls) == 1


@pytest.mark.parametrize('change', [dict(status='ready'), dict(gateway='fixture-sync')])
def test_not_a_remote_active_ask(tmp_path, change):
    mirror, store, _, hr = fixture(tmp_path)
    row = remote_row(hr)
    row.update(change)
    mirror.remote.cards = lambda owner: [row]
    mirror.remote.reconcile(OWNER)
    assert not store.requests(OWNER)


@pytest.mark.parametrize('change', ['provenance', 'execution', 'permissions', 'choice'])
def test_other_binding_cannot_authorize_execution_or_unknown_choice(tmp_path, change):
    mirror, store, _, hr = fixture(tmp_path)
    mirror.remote.cards = lambda owner: [remote_row(hr)]
    mirror.remote.reconcile(OWNER)
    request = store.requests(OWNER)[0]
    op = mirror.decide_other(OWNER, dict(requestId=request['requestId'], expectedRevision=1,
        idempotencyKey='other-answer', choiceId='answer-text', revisionText='Only record this'))['operation']
    if change == 'provenance':
        op['provenance'] = {}
    if change == 'execution':
        op['intent']['executionAuthorized'] = True
    if change == 'permissions':
        op['intent']['permissionGrants'] = ['network']
    if change == 'choice':
        op['decision']['choiceId'] = 'unknown-choice'
    with pytest.raises(core.ActionError):
        mirror.remote.envelope(op, mirror._bound(op))


def test_remote_binding_uses_owner_not_source_and_is_atomic(tmp_path):
    mirror, store, _, hr = fixture(tmp_path)
    mirror.remote.cards = lambda owner: [remote_row(hr)]
    mirror.remote.reconcile(OWNER)
    request = store.requests(OWNER)[0]
    assert request['scope']['sourceRefs'] == [dict(type='kanban', gatewayId='remote',
                                                  profile='worker', board='other', id='t_remote')]
    op = decide(store, request)
    envelope = mirror.remote.envelope(op, mirror._bound(op))
    assert envelope['key'] == 'worker/other/t_remote'
    assert envelope['args']['answer'] == dict(ownerGateway='remote', ownerProfile='worker', board='other',
        task='t_remote', generation='a' * 64, choice='green', operationId=op['operationId'])
    assert envelope['args']['source'] == dict(gateway='fixture-sync', profile=OWNER['profile'],
                                             session=OWNER['storedSessionId'])
    assert mirror.present(request)['presentation']['sourceRef']['gatewayId'] == 'remote'
    assert not op['intent']['executionAuthorized'] and op['intent']['permissionGrants'] == []


@pytest.mark.parametrize('changed', [dict(stale=True), dict(card_answer_generation=None)])
def test_no_remote_tiles_without_fresh_portable_generation(tmp_path, changed):
    mirror, store, _, hr = fixture(tmp_path)
    row = remote_row(hr)
    row.update(changed)
    mirror.remote.cards = lambda owner: [row]
    mirror.remote.reconcile(OWNER)
    assert store.requests(OWNER) == []


@pytest.mark.parametrize('error', [KeyError('bad remote row'), TypeError('bad choices'), RuntimeError('reader failure')])
def test_remote_reader_failure_does_not_suppress_local_cards(tmp_path, error):
    mirror, store, _, _ = fixture(tmp_path)
    def unavailable(owner):
        raise error
    mirror.remote.cards = unavailable
    mirror.reconcile(OWNER)
    assert store.requests(OWNER)


@pytest.mark.parametrize('choices', [[{}], ['bad'], [{'label': 'A'}], {'bad': 'shape'}])
def test_malformed_remote_choices_preserve_open_request_and_local_cards(tmp_path, choices):
    mirror, store, _, hr = fixture(tmp_path)
    mirror.remote.cards = lambda owner: [remote_row(hr)]
    mirror.remote.reconcile(OWNER)
    request = store.requests(OWNER)[0]
    malformed = dict(hr, choices=choices)
    mirror.remote.cards = lambda owner: [remote_row(malformed)]
    mirror.reconcile(OWNER)
    assert store.get(OWNER, request['requestId'])['state'] == 'open'
    assert len(store.requests(OWNER)) > 1


def test_stale_remote_projection_retains_existing_tile(tmp_path):
    mirror, store, _, hr = fixture(tmp_path)
    mirror.remote.cards = lambda owner: [remote_row(hr)]
    mirror.remote.reconcile(OWNER)
    request = store.requests(OWNER)[0]
    mirror.remote.cards = lambda owner: [remote_row(hr, stale=True)]
    mirror.reconcile(OWNER)
    assert store.get(OWNER, request['requestId'])['state'] == 'open'
    mirror.remote.cards = lambda owner: [remote_row(hr)]
    mirror.remote.reconcile(OWNER)
    assert store.get(OWNER, request['requestId'])['state'] == 'open'


def test_unavailable_membership_is_not_withdrawal_but_confirmed_departure_is(tmp_path):
    mirror, store, _, hr = fixture(tmp_path)
    mirror.remote.cards = lambda owner: [remote_row(hr)]
    mirror.remote.reconcile(OWNER)
    request = store.requests(OWNER)[0]
    def unavailable(owner):
        raise ImportError('optional reader unavailable')
    mirror.remote.cards = unavailable
    mirror.remote.reconcile(OWNER)
    assert store.get(OWNER, request['requestId'])['state'] == 'open'
    mirror.remote.cards = lambda owner: []
    mirror.remote.reconcile(OWNER)
    assert store.get(OWNER, request['requestId'])['state'] != 'open'


@pytest.mark.parametrize('other,stale', [(False, False), (False, True), (True, False), (True, True)])
def test_mirrored_real_store_answer_receipt_against_owner(tmp_path, monkeypatch, request, other, stale):
    guards = [getattr(p, '__file__', '') for p in request.config.pluginmanager.get_plugins()]
    if not any(str(p).endswith('/tests/conftest.py') and 'user-steps' not in str(p) for p in guards):
        pytest.skip('Run with isolated core tests.conftest guards: ' + repr(guards))
    from hermes_cli import kanban_card_answer as ca, kanban_db as kb
    from hermes_cli.kanban_db_connect import connect_closing
    from tests.hermes_cli.test_card_answer_courier_integration import World, helpers

    courier_path = ROOT.parents[1] / 'courier.py'
    if not courier_path.is_file():
        pytest.skip('Set HERMES_HUB_CLIENT_REAL to staged M1+ courier')
    courier = load_module('mirror_courier', Path(courier_path))
    # Core fixtures own all filesystem and board guards. Owner fixture is a real primary DB.
    owner_home = tmp_path / 'owner-home'
    owner_home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(owner_home))
    monkeypatch.setattr(Path, 'home', lambda: owner_home)
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    token = set_hermes_home_override(str(owner_home))
    world = None
    try:
        kb.init_db(owner_home / 'kanban.db')
        with connect_closing(owner_home / 'kanban.db') as conn:
            conn.execute('CREATE TABLE kanban_sync_state(gateway_id TEXT,mode TEXT)')
            conn.execute("INSERT INTO kanban_sync_state VALUES ('owner','primary')")
            conn.commit()
            answer = helpers.setup_answer(conn)
            snap = ca.snapshot(conn, answer['task'])
        world = World(courier, tmp_path, owner_home, monkeypatch)
        source = dict(gatewayId='install:source-install', profile='work', storedSessionId='chat-one')
        store = core.ActionStore(world.homes['source'] / 'user-steps/actions.sqlite3')
        mod = load_module('remote_integration_mirror', ROOT / 'kanban_mirror.py')
        mirror = mod.Mirror(core, store, boards=[], session_exists=lambda s: True, tip=lambda s: s)
        mirror.remote.gateway = lambda: 'origin'
        mirror.remote.cards = lambda owner: [dict(gateway='owner', profile='default', board='default',
            id=answer['task'], session='chat-one', status='blocked', human_request=snap['ask'],
            card_answer_generation=snap['generation'], title='Pick a plan')]
        request = mirror.remote.request_for(source, dict(ownerGateway='owner', ownerProfile='default',
                                                       board='default', task=answer['task']))
        assert request == mirror.remote.request_for(source, dict(ownerGateway='owner', ownerProfile='default',
                                                                 board='default', task=answer['task']))
        payload = dict(requestId=request['requestId'], expectedRevision=1, idempotencyKey='user-click',
                       choiceId='answer-text' if other else 'card-0',
                       revisionText='plan-a' if other else request['choices'][0]['label'])
        result = mirror.decide_other(source, payload) if other else store.decide(source, payload)
        op = result['operation']
        if stale:
            with connect_closing(owner_home / 'kanban.db') as conn:
                kb._append_event(conn, answer['task'], 'blocked', {'reason': 'new generation'})
                conn.commit()

        remote_module = load_module('remote_integration_courier', ROOT / 'remote_cards.py')
        exchange = remote_module.Courier.__new__(remote_module.Courier)
        source_db = Path(world.dbs['source'].execute('PRAGMA database_list').fetchone()[2])
        def send_now(client, path, timeout):
            world.service.deliver(world.dbs['source'], 'origin')
            world.tick('owner')
            world.tick('source')
            world.tick('owner')
        monkeypatch.setattr(courier, 'load_config', lambda: {'hub_url': 'https://example.test'})
        monkeypatch.setattr(courier, 'read_key', lambda: 'fixture-key')
        monkeypatch.setattr(courier, 'Api', lambda *args: SimpleNamespace(
            call=lambda method, path: (200, {'request': world.service.get(path.rsplit('/', 1)[1])})))
        exchange.view = SimpleNamespace(load_client=lambda: courier, db_path=lambda: source_db,
                                        _send_now=send_now, SEND_WAIT_S=0)
        mirror.remote.courier = exchange
        if stale:
            with pytest.raises(core.ActionError, match='refused'):
                mirror.deliver(op)
            store.dispatch(source, op['operationId'], mirror)
            assert store.operation(source, op['operationId'])['operation']['dispatchState'] == 'cancelled'
        else:
            receipt = mirror.deliver(op)
            assert receipt['choice'] == 'plan-a'
            assert receipt.get('mode') == ('comment' if other else None)
            assert receipt['generation'] == snap['generation']
            assert receipt['operationId'] == op['operationId']
            assert mirror.readback(op, receipt)
            assert mirror.deliver(op) == receipt
            delivered = store.dispatch(source, op['operationId'], mirror)['operation']
            assert delivered['dispatchState'] == 'delivered'
            if other:
                mirror.remote.reconcile(source)
                opens = [r for r in store.requests(source) if r['state'] == 'open']
                assert len(opens) == 1 and opens[0]['requestId'] != request['requestId']
        with connect_closing(owner_home / 'kanban.db') as conn:
            task = kb.get_task(conn, answer['task'])
            comments = helpers.comments(conn, answer['task'])
            assert task.status == ('blocked' if other or stale else 'ready')
            assert len(comments) == (0 if stale else 1)
            if other and not stale:
                assert task.human_request
                events = kb.list_events(conn, answer['task'])
                assert not any(e.kind == 'unblocked' for e in events)
    finally:
        if world:
            for db in world.dbs.values():
                db.close()
        reset_hermes_home_override(token)


def test_membership_hydrates_only_exact_chat_gateway_profile_board_target(tmp_path, monkeypatch):
    mirror, _, _, hr = fixture(tmp_path)
    module = sys.modules[mirror.remote.__class__.__module__] if mirror.remote.__class__.__module__ in sys.modules else load_module('remote_membership', ROOT / 'remote_cards.py')
    exact = dict(remote_row(hr), origin_gateway='fixture-sync', origin_profile=OWNER['profile'])
    membership = [exact, dict(exact, id='wrong-chat', session='someone-else'),
                  dict(exact, id='wrong-origin', origin_gateway='another-origin'),
                  dict(exact, id='wrong-profile', origin_profile='another-profile'),
                  dict(exact, id='local-target', gateway='fixture-sync')]
    owner = dict(OWNER, gatewayId='install:' + '0' * 32)
    try:
        reader = importlib.import_module('hermes_cli.kanban_session_activity')
        monkeypatch.setattr(reader, '_remote_cards', lambda *args: membership)
    except ImportError:
        monkeypatch.setitem(sys.modules, 'hermes_cli.kanban_session_activity',
                            SimpleNamespace(_remote_cards=lambda *args: membership))
    candidates = [(r['gateway'], '/'.join(r[k] for k in ('profile', 'board', 'id')), r, None, False)
                  for r in membership]
    candidates += [('remote', 'wrong/other/t_remote', dict(exact, profile='wrong'), None, False),
                  ('remote', 'worker/wrong/t_remote', dict(exact, board='wrong'), None, False),
                  ('stranger', 'worker/other/t_remote', dict(exact, gateway='stranger'), None, False)]
    closed = []
    view = SimpleNamespace(gateway='fixture-sync', rows=lambda coll, pick: [c[2] for c in pick(candidates)],
                           conn=SimpleNamespace(close=lambda: closed.append(True)))
    monkeypatch.setattr(module, 'load_file', lambda path: SimpleNamespace(open_view=lambda: view))
    assert module.linked_cards(owner) == [exact]
    assert closed == [True]


def test_action_queue_reads_the_last_good_links_projection(tmp_path, monkeypatch):
    """2026-11-23: a strict links read queued 10-60 s behind ``_links_lock`` and Desktop showed "Action queue
    unavailable". ``linked_cards`` (the /user-steps/requests path) must ask for the last good projection."""
    fixture(tmp_path)
    module = load_module('remote_membership_stale', ROOT / 'remote_cards.py')
    calls = []

    def remote_cards(ids, statuses, stale_ok):
        calls.append(stale_ok)
        return []
    try:
        reader = importlib.import_module('hermes_cli.kanban_session_activity')
        monkeypatch.setattr(reader, '_remote_cards', remote_cards)
    except ImportError:
        monkeypatch.setitem(sys.modules, 'hermes_cli.kanban_session_activity',
                            SimpleNamespace(_remote_cards=remote_cards))
    view = SimpleNamespace(gateway='fixture-sync', rows=lambda coll, pick: [],
                           conn=SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(module, 'load_file', lambda path: SimpleNamespace(open_view=lambda: view))
    assert module.linked_cards(dict(OWNER, gatewayId='install:' + '0' * 32)) == []
    assert calls == [True], 'the action queue waited for a strict links rebuild'


@pytest.mark.parametrize('state', ['rejected', 'expired', 'failed'])
def test_owner_refusal_retires_and_remints_without_retry(tmp_path, state):
    mirror, store, _, hr = fixture(tmp_path)
    mirror.remote.cards = lambda owner: [remote_row(hr)]
    mirror.remote.reconcile(OWNER)
    old = store.requests(OWNER)[0]
    op = decide(store, old)
    calls = []
    class Transport:
        def exchange(self, envelope, rid):
            calls.append(rid)
            return dict(copy.deepcopy(envelope), id=rid, state=state, **{'from': 'gw:fixture-sync'})
    mirror.remote.courier = Transport()
    store.dispatch(OWNER, op['operationId'], mirror)
    assert store.operation(OWNER, op['operationId'])['operation']['dispatchState'] == 'cancelled'
    store.reconcile(OWNER, mirror)
    assert len(calls) == 1
    mirror.remote.reconcile(OWNER)
    open_rows = [r for r in store.requests(OWNER) if r['state'] == 'open']
    assert len(open_rows) == 1 and open_rows[0]['requestId'] != old['requestId']


@pytest.mark.parametrize('departure', [False, True])
def test_changed_remote_generation_settles_recorded_operation(tmp_path, departure):
    mirror, store, _, hr = fixture(tmp_path)
    row = remote_row(hr)
    mirror.remote.cards = lambda owner: [row]
    mirror.remote.reconcile(OWNER)
    old = store.requests(OWNER)[0]
    op = decide(store, old)
    mirror.remote.courier = SimpleNamespace(read=lambda envelope, rid: None)
    mirror.remote.cards = lambda owner: [] if departure else [dict(row, card_answer_generation='b' * 64)]
    mirror.remote.reconcile(OWNER)
    assert store.operation(OWNER, op['operationId'])['operation']['dispatchState'] == 'cancelled'
    assert store.get(OWNER, old['requestId'])['state'] == 'cancelled'
    # 404 is absence, not a terminal fact: the target stays held (no remint) until stable refusal evidence.
    assert [r for r in store.requests(OWNER) if r['state'] == 'open'] == []


@pytest.mark.parametrize('bad', ['stale', 'duplicate', 'malformed', 'action-validation'])
def test_bad_remote_row_preserves_only_its_tile_and_publishes_healthy_rows(tmp_path, bad):
    mirror, store, _, hr = fixture(tmp_path)
    row = remote_row(hr)
    mirror.remote.cards = lambda owner: [row]
    mirror.remote.reconcile(OWNER)
    old = store.requests(OWNER)[0]
    broken = (dict(row, stale=True) if bad == 'stale' else dict(row, human_request=dict(hr,
        choices=[hr['choices'][0]] * 2 if bad == 'duplicate' else [{}])))
    if bad == 'action-validation':
        broken = dict(row, title={'invalid': 'title'}, card_answer_generation='b' * 64)
    mirror.remote.cards = lambda owner: [broken, dict(row, id='t_healthy')]
    mirror.remote.reconcile(OWNER)
    assert store.get(OWNER, old['requestId'])['state'] == 'open'
    assert len([r for r in store.requests(OWNER) if r['state'] == 'open']) == 2


def test_remote_default_board_never_uses_local_binding_reader(tmp_path):
    mirror, store, _, hr = fixture(tmp_path)
    row = dict(remote_row(hr), board='default')
    mirror.remote.cards = lambda owner: [row]
    mirror.remote.reconcile(OWNER)
    old = store.requests(OWNER)[0]
    mirror._reconcile_binding(OWNER, mirror.binding(OWNER, old['requestId']))
    assert store.get(OWNER, old['requestId'])['state'] == 'open'


def test_receipt_mode_must_match_choice_envelope(tmp_path):
    mirror, store, _, hr = fixture(tmp_path)
    mirror.remote.cards = lambda owner: [remote_row(hr)]
    mirror.remote.reconcile(OWNER)
    op = decide(store, store.requests(OWNER)[0])
    class Transport:
        def exchange(self, envelope, rid):
            receipt = dict(copy.deepcopy(envelope['args']['answer']), mode='comment',
                originGateway='fixture-sync', destination='kanban_ask', executionStarted=False,
                decisionProvenance='recorded_desktop_operation')
            return dict(copy.deepcopy(envelope), id=rid, state='done', result=receipt,
                        **{'from': 'gw:fixture-sync'})
    mirror.remote.courier = Transport()
    with pytest.raises(OSError, match='receipt'):
        mirror.deliver(op)


def test_delivered_other_remints_same_generation(tmp_path):
    mirror, store, _, hr = fixture(tmp_path)
    mirror.remote.cards = lambda owner: [remote_row(hr)]
    mirror.remote.reconcile(OWNER)
    old = store.requests(OWNER)[0]
    op = mirror.decide_other(OWNER, dict(requestId=old['requestId'], expectedRevision=1,
        idempotencyKey='other-remint', choiceId='answer-text', revisionText='Need context'))['operation']
    class Receiver:
        def deliver(self, op): return {'ok': True}
        def readback(self, op, receipt): return True
    store.dispatch(OWNER, op['operationId'], Receiver())
    mirror.remote.reconcile(OWNER)
    opens = [r for r in store.requests(OWNER) if r['state'] == 'open']
    assert len(opens) == 1 and opens[0]['requestId'] != old['requestId']


def test_partial_links_membership_is_unavailable_not_departure(tmp_path, monkeypatch):
    """A replica-only fallback (origin custody unreadable) is partial: an ask it omits must not be withdrawn.
    ``linked_cards`` raises, and reconcile keeps the open request."""
    mirror, store, _, hr = fixture(tmp_path)
    module = load_module('remote_membership_partial', ROOT / 'remote_cards.py')
    exact = dict(remote_row(hr), origin_gateway='fixture-sync', origin_profile=OWNER['profile'])
    partial = [dict(exact, stale=True, partial_source_unknown='origin_ledger_unavailable')]
    try:
        reader = importlib.import_module('hermes_cli.kanban_session_activity')
        monkeypatch.setattr(reader, '_remote_cards', lambda *args: partial)
    except ImportError:
        monkeypatch.setitem(sys.modules, 'hermes_cli.kanban_session_activity',
                            SimpleNamespace(_remote_cards=lambda *args: partial))
    with pytest.raises(OSError, match='partial'):
        module.linked_cards(dict(OWNER, gatewayId='install:' + '0' * 32))

    # end to end: an open remote ask, then a pass whose membership read is partial (raises): kept, not withdrawn
    mirror.remote.cards = lambda owner: [remote_row(hr)]
    mirror.remote.reconcile(OWNER)
    before = [r["requestId"] for r in store.requests(OWNER)]
    assert before

    def partial_read(owner):
        raise OSError('Remote ask membership partial: origin_ledger_unavailable')
    mirror.remote.cards = partial_read
    try:
        mirror.remote.reconcile(OWNER)
    except OSError:
        pass  # surfaced as unavailable to the caller is fine; what matters is no withdrawal
    assert [r["requestId"] for r in store.requests(OWNER)] == before, 'a partial membership read withdrew an ask'
