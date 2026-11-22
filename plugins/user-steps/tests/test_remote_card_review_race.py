from pathlib import Path
from types import SimpleNamespace
import pytest
from test_withdrawal import ROOT, core, load_module
import json


@pytest.mark.parametrize('refresh', ['none', 'departure', 'reblock'])
def test_successful_owner_delivery_not_retired_by_replica_refresh(tmp_path, monkeypatch, request, refresh):
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
        refreshed = []
        def send_now(client, path, timeout):
            world.service.deliver(world.dbs['source'], 'origin')
            world.tick('owner')
            world.tick('source')
            world.tick('owner')
            # Owner applied and published ready while the source awaits readback.
            if refreshed:
                return
            refreshed.append(True)
            # Card dispatch owns an exclusive lease through settlement. A
            # competing retry must not start another receiver/terminal path.
            retry = store.dispatch(source, op['operationId'], mirror)['operation']
            assert retry['attempts'] == 1
            assert retry['dispatchState'] == 'pending'
            if refresh == 'departure':
                mirror.remote.cards = lambda owner: []
            elif refresh == 'reblock':
                with connect_closing(owner_home / 'kanban.db') as conn:
                    with kb.write_txn(conn):
                        conn.execute('UPDATE tasks SET status=?, human_request=? WHERE id=?',
                                     ('blocked', json.dumps(snap['ask']), answer['task']))
                        kb._append_event(conn, answer['task'], 'blocked', {'reason': 'Owner asks again'})
                    fresh = ca.snapshot(conn, answer['task'])
                mirror.remote.cards = lambda owner: [dict(gateway='owner', profile='default', board='default',
                    id=answer['task'], session='chat-one', status='blocked', human_request=fresh['ask'],
                    card_answer_generation=fresh['generation'], title='Pick again')]
            if refresh != 'none':
                mirror.remote.reconcile(source)
                assert len([r for r in store.requests(source) if r['state'] == 'open']) == 1
        monkeypatch.setattr(courier, 'load_config', lambda: {'hub_url': 'https://example.test'})
        monkeypatch.setattr(courier, 'read_key', lambda: 'fixture-key')
        monkeypatch.setattr(courier, 'Api', lambda *args: SimpleNamespace(
            call=lambda method, path: (200, {'request': world.service.get(path.rsplit('/', 1)[1])})))
        exchange.view = SimpleNamespace(load_client=lambda: courier, db_path=lambda: source_db,
                                        _send_now=send_now, SEND_WAIT_S=0)
        mirror.remote.courier = exchange
        delivered = store.dispatch(source, op['operationId'], mirror)['operation']
        with connect_closing(owner_home / 'kanban.db') as conn:
            assert kb.get_task(conn, answer['task']).status == ('blocked' if refresh == 'reblock' else 'ready')
            assert len(helpers.comments(conn, answer['task'])) == 1
        # Exact receipt is still independently obtainable, yet dispatch discarded it.
        assert mirror.readback(op, mirror.deliver(op))
        assert delivered['dispatchState'] == 'delivered', delivered
        with connect_closing(owner_home / 'kanban.db') as conn:
            task = kb.get_task(conn, answer['task'])
            comments = helpers.comments(conn, answer['task'])
            assert task.status == ('blocked' if refresh == 'reblock' else 'ready')
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
