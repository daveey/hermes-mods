import pytest
from test_remote_card_courier import remote_row
from test_kanban_mirror import fixture, decide
from test_withdrawal import OWNER

@pytest.mark.parametrize('recorded', [False, True])
def test_title_refresh_leaves_one_current_tile(tmp_path, recorded):
    mirror, store, _, hr = fixture(tmp_path)
    row = remote_row(hr)
    mirror.remote.cards = lambda owner: [row]
    mirror.remote.reconcile(OWNER)
    old = store.requests(OWNER)[0]
    op = decide(store, old) if recorded else None
    row['title'] = 'Renamed card, unchanged ask'
    mirror.remote.reconcile(OWNER)
    opens = [r for r in store.requests(OWNER) if r['state'] == 'open']
    assert len(opens) == 1, [(r['title'], bool(r.get('resolution'))) for r in opens]
    assert opens[0]['requestId'] == old['requestId']
    assert mirror.present(opens[0])['title'] == row['title']
    assert store.get(OWNER, old['requestId'])['title'] == old['title']
    if recorded:
        assert opens[0]['resolution']['operationId'] == op['operationId']

@pytest.mark.parametrize('action', [42, {'malformed':'action'}, ['action']])
def test_malformed_action_preserves_old_and_does_not_poison_feed(tmp_path, action):
    mirror, store, _, hr = fixture(tmp_path)
    row = remote_row(hr)
    mirror.remote.cards = lambda owner: [row]
    mirror.remote.reconcile(OWNER)
    old = store.requests(OWNER)[0]
    row['human_request'] = dict(hr, action=action)
    row['card_answer_generation'] = 'b'*64
    mirror.remote.cards = lambda owner: [row, dict(remote_row(hr), id='t_healthy')]
    mirror.reconcile(OWNER)
    for request in store.requests(OWNER):
        mirror.present(request)
    assert store.get(OWNER, old['requestId'])['state'] == 'open'

@pytest.mark.parametrize('state', ['rejected','expired','failed'])
def test_terminal_via_actual_dispatch_remains_cancelled(tmp_path,state):
    mirror, store, _, hr = fixture(tmp_path)
    mirror.remote.cards = lambda owner: [remote_row(hr)]
    mirror.remote.reconcile(OWNER)
    op=decide(store,store.requests(OWNER)[0])
    calls=[]
    class T:
        def exchange(self,e,rid):
            calls.append(rid)
            return dict(e,id=rid,state=state,**{'from':'gw:fixture-sync'})
    mirror.remote.courier=T()
    assert store.dispatch(OWNER,op['operationId'],mirror)['operation']['dispatchState']=='cancelled'
    for _ in range(3): store.reconcile(OWNER,mirror)
    assert len(calls)==1

@pytest.mark.parametrize('action', [None, 42, {'malformed': 'action'}, ['action']])
def test_real_requests_feed_isolates_malformed_action(tmp_path, action):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from test_withdrawal import ROOT, load_module
    mirror, store, path, hr = fixture(tmp_path)
    row=remote_row(hr)
    mirror.remote.cards=lambda owner:[row]
    mirror.remote.reconcile(OWNER)
    old=store.requests(OWNER)[0]
    if action is not None:
        row['human_request']=dict(hr,action=action)
        row['card_answer_generation']='b'*64
    mirror.remote.cards=lambda owner:[row,dict(remote_row(hr),id='t_healthy')]
    api=load_module('review_feed_api',ROOT/'dashboard/plugin_api.py')
    api.RETRY_INTERVAL_S=0
    app=FastAPI()
    app.include_router(api.create_router(ledger_dir=tmp_path/'ledger',state_db=tmp_path/'missing.db',
        action_store=store,owner_resolver=lambda sid:dict(OWNER,storedSessionId=sid),
        kanban_boards=[('default',path)],kanban_mirror=mirror))
    with TestClient(app,raise_server_exceptions=False) as client:
        first=client.get('/requests',params={'session':'session-a'})
        assert first.status_code==200,first.text
        # The poll queues the remote pass on a background worker; the new remote ask shows once it finished.
        assert type(mirror).reconcile_poll.__globals__['_background_idle'](5)
        response=client.get('/requests',params={'session':'session-a'})
        assert response.status_code==200,response.text
        assert type(mirror).reconcile_poll.__globals__['_background_idle'](5)  # a deferral re-queues: let it end
        refs = [ref['id'] for item in response.json()['requests']
                for ref in item['scope']['sourceRefs'] if ref.get('type') == 'kanban']
        assert 't_healthy' in refs
        assert 't_test' in refs
        assert old['requestId'] in [item['requestId'] for item in response.json()['requests']]
    assert store.get(OWNER,old['requestId'])['state']=='open'
