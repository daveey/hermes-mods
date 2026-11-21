"""Real ActionStore -> remote mirror -> M1 courier journals -> owner transaction.

Authenticated service/OS identity are offline fixtures, not a live HTTPS proof.
Run with run-core-integration.sh and HERMES_HUB_CLIENT_REAL pointing to M1+ client.
"""
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
    assert envelope['args']['source'] == dict(gateway=OWNER['gatewayId'], profile=OWNER['profile'],
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
        source = dict(gatewayId='origin', profile='work', storedSessionId='chat-one')
        store = core.ActionStore(world.homes['source'] / 'user-steps/actions.sqlite3')
        mod = load_module('remote_integration_mirror', ROOT / 'kanban_mirror.py')
        mirror = mod.Mirror(core, store, boards=[], session_exists=lambda s: True, tip=lambda s: s)
        mirror.remote.cards = lambda owner: [dict(gateway='owner', profile='default', board='default',
            id=answer['task'], session='chat-one', status='blocked', human_request=snap['ask'],
            card_answer_generation=snap['generation'], title='Pick a plan')]
        mirror.remote.reconcile(source)
        request = store.requests(source)[0]
        payload = dict(requestId=request['requestId'], expectedRevision=1, idempotencyKey='user-click',
                       choiceId='answer-text' if other else 'card-0',
                       revisionText='plan-a' if other else request['choices'][0]['label'])
        result = mirror.decide_other(source, payload) if other else store.decide(source, payload)
        op = result['operation']
        if stale:
            with connect_closing(owner_home / 'kanban.db') as conn:
                kb._append_event(conn, answer['task'], 'blocked', {'reason': 'new generation'})
                conn.commit()

        class Exchange:
            def exchange(self, envelope, rid):
                with world.host('source'):
                    courier.queue_request(world.dbs['source'], envelope['to_gw'], envelope['coll'], envelope['key'],
                                          envelope['action'], envelope['args'], rid=rid)
                    world.dbs['source'].commit()
                    world.service.deliver(world.dbs['source'], 'origin')
                world.tick('owner')
                world.tick('source')
                world.tick('owner')
                return world.service.get(rid)

        mirror.remote.courier = Exchange()
        if stale:
            with pytest.raises(core.ActionError, match='refused'):
                mirror.deliver(op)
        else:
            receipt = mirror.deliver(op)
            assert receipt['choice'] == 'plan-a'
            assert receipt.get('mode') == ('comment' if other else None)
            assert receipt['generation'] == snap['generation']
            assert receipt['operationId'] == op['operationId']
            assert mirror.readback(op, receipt)
            assert mirror.deliver(op) == receipt
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
    exact = remote_row(hr)
    membership = [exact, dict(exact, id='wrong-chat', session='someone-else'),
                  dict(exact, id='wrong-origin', origin_gateway='another-origin'),
                  dict(exact, id='wrong-profile', origin_profile='another-profile')]
    try:
        reader = importlib.import_module('hermes_cli.kanban_session_activity')
        monkeypatch.setattr(reader, '_remote_cards', lambda *args: membership)
    except ImportError:
        monkeypatch.setitem(sys.modules, 'hermes_cli.kanban_session_activity',
                            SimpleNamespace(_remote_cards=lambda *args: membership))
    candidates = [('remote', 'worker/other/t_remote', exact, None, False),
                  ('remote', 'wrong/other/t_remote', dict(exact, profile='wrong'), None, False),
                  ('remote', 'worker/wrong/t_remote', dict(exact, board='wrong'), None, False),
                  ('stranger', 'worker/other/t_remote', dict(exact, gateway='stranger'), None, False)]
    closed = []
    view = SimpleNamespace(rows=lambda coll, pick: [c[2] for c in pick(candidates)],
                           conn=SimpleNamespace(close=lambda: closed.append(True)))
    monkeypatch.setattr(module, 'load_file', lambda path: SimpleNamespace(open_view=lambda: view))
    assert module.linked_cards(OWNER) == [exact]
    assert closed == [True]
