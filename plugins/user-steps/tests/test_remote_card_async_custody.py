from pathlib import Path
from types import SimpleNamespace
import pytest
from test_withdrawal import ROOT, core, load_module
import json
import copy
import threading


@pytest.mark.parametrize('refresh', ['no-race', 'race'])
def test_async_outbox_absence_observation_race(tmp_path, monkeypatch, request, refresh):
    other = stale = False
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
    release = None
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

        remote_module = load_module('remote_integration_courier', ROOT / 'remote_cards.py')
        exchange = remote_module.Courier.__new__(remote_module.Courier)
        source_db = Path(world.dbs['source'].execute('PRAGMA database_list').fetchone()[2])
        # Exercise actual _send_now and actual send_queued with a delayed POST.
        hv = load_module('review_hubview', ROOT.parent / 'hub-sync/hubview.py')
        started, release, posted = threading.Event(), threading.Event(), threading.Event()
        monkeypatch.setattr(courier, 'shared_any_on', lambda db: False)
        def send_now(client, path, timeout):
            result = hv._send_now(client, path, 0)
            assert started.wait(10)
            assert result is None
        def finish_post():
            release.set()
            assert posted.wait(10)

        phase = {'retiring': False, 'raced': False}
        def advance():
            finish_post()
            world.tick('owner')
            world.tick('source')
            world.tick('owner')
        def call(method, path, body=None):
            if method == 'POST':
                assert path == '/v2/sync/requests'
                started.set()
                assert release.wait(15)
                world.service.requests[body['id']] = {**copy.deepcopy(body), 'from': 'gw:origin', 'state': 'queued'}
                posted.set()
                return 201, {'request': world.service.get(body['id'])}
            assert method == 'GET'
            rid = path.rsplit('/', 1)[1]
            record = world.service.get(rid)
            response = (200, {'request': record}) if record else (404, {})
            if phase['retiring'] and not phase['raced'] and refresh == 'race':
                # A background courier finishes while the GET's already-produced
                # absence response is travelling back to the reconciler.
                phase['raced'] = True
                assert response == (404, {})
                advance()
                assert world.service.get(rid)['state'] == 'done'
            return response
        monkeypatch.setattr(courier, 'load_config', lambda: {'hub_url': 'https://example.test'})
        monkeypatch.setattr(courier, 'read_key', lambda: 'fixture-key')
        monkeypatch.setattr(courier, 'Api', lambda *args: SimpleNamespace(
            call=call))
        exchange.view = SimpleNamespace(load_client=lambda: courier, db_path=lambda: source_db,
                                        _send_now=send_now, SEND_WAIT_S=0)
        mirror.remote.courier = exchange
        initial = store.dispatch(source, op['operationId'], mirror)['operation']
        assert initial['dispatchState'] == 'failed'
        assert world.dbs['source'].execute('SELECT count(*) FROM sent').fetchone()[0] == 1
        assert not world.service.requests
        mirror.remote.cards = lambda owner: []
        phase['retiring'] = True
        mirror.remote.reconcile(source)
        settled = store.operation(source, op['operationId'])['operation']
        if refresh == 'no-race':
            # The envelope is still local (possibly mid-POST): absence is not proof, so the op is only
            # `retiring` (proofs refused, held, not reminted), and a later receipt converts it to delivered.
            assert settled['dispatchState'] == 'cancelled' and settled['retired']['evidence'] == 'remote-retiring'
            assert not [r for r in store.requests(source) if r['state'] == 'open']
            advance()
            # Cancel-before-proof control: the owner's source verification now refuses, so nothing commits.
            with connect_closing(owner_home / 'kanban.db') as conn:
                assert kb.get_task(conn, answer['task']).status == 'blocked'
                assert len(helpers.comments(conn, answer['task'])) == 0
            rid = mirror.remote._rid(op)
            mirror.remote.reconcile(source)
            assert store.operation(source, op['operationId'])['operation']['dispatchState'] != 'delivered'
        else:
            with connect_closing(owner_home / 'kanban.db') as conn:
                assert kb.get_task(conn, answer['task']).status == 'ready'
                assert len(helpers.comments(conn, answer['task'])) == 1
            rid = mirror.remote._rid(op)
            record = world.service.get(rid)
            receipt = mirror.remote._receipt(mirror.remote.envelope(op, mirror._bound(op)), rid, record)
            assert receipt['operationId'] == op['operationId']
            mirror.remote.reconcile(source)
            assert store.operation(source, op['operationId'])['operation']['dispatchState'] == 'delivered', settled
    finally:
        if release is not None:
            release.set()
        if world:
            for db in world.dbs.values():
                db.close()
        reset_hermes_home_override(token)
