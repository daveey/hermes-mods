"""Deferred outcomes must replay exact retained evidence, never clipped prose."""
import json
import socket
from contextlib import closing

import pytest
from test_self_heal import sh, core, load, ROOT
from test_detector_recheck_pending import store, _retry

sg = load('coach_outcome_signals_test', ROOT / 'signals.py')
NOW = 1794583198
AT = NOW - 40000
REPLY = 'Context. ' * 45 + 'One of my cards is still open: the card-placement fix (PR #1851).'
COMMAND = 'cd ~; token-broker exec --scope user/github --reason "PR1851 state" -- gh pr view 1851 -R example/project --json state,mergedAt,mergeStateStatus'


def fixture(home, *, unread=False, ambiguous=False, continuation=False):
    n = dict(event='reply_nudge', rule='pr_state_claim', nudge_id='n_test', session_id='original',
             ts=AT, host=socket.gethostname(), p=1.0, evidence='PR #1851 is still open.', outcome=True)
    o = dict(event='reply_nudge_outcome', rule='pr_state_claim', nudge_id='n_test',
             session_id='continued' if continuation else 'original', ts=AT+16, p=1.0,
             resolved=False, judged=False, evidence='One of my cards is still open: the card-placement fix (PR #1851).', reply=REPLY[:300])
    rec = {**n, 'reply':o['reply'], 'outcome_evidence':o['evidence'], 'outcome_status':'', 'outcome_item_ids':[]}
    d = sg.to_defect({**sg._handoff_signal(rec, 'the revised reply still failed pr_state_claim (outcome p=1.0)'), 'host':n['host']})
    (home/'coach').mkdir(exist_ok=True)
    (home/'coach/log.jsonl').write_text('\n'.join(map(json.dumps,[n,o]))+'\n')
    import sqlite3
    with closing(sqlite3.connect(home/'state.db')) as db, db:
        db.execute('create table messages(id integer, session_id text, role text, content text, tool_calls text, tool_call_id text, timestamp real)')
        calls = json.dumps([{'id':'call1','function':{'name':'terminal','arguments':json.dumps({'command': 'date' if unread else COMMAND})}}])
        rows = [(1,o['session_id'],'assistant','',calls,None,AT+6),
                (2,o['session_id'],'tool',json.dumps({'output':'OPEN', 'exit_code':0}),None,'call1',AT+8),
                (3,o['session_id'],'assistant',REPLY,None,None,AT+14),
                (4,o['session_id'],'assistant',REPLY,None,None,AT+14)]
        if ambiguous:
            rows.append((5,o['session_id'],'assistant',REPLY+' Different.',None,None,AT+15))
        db.executemany('insert into messages values(?,?,?,?,?,?,?)',rows)
    return d


@pytest.mark.parametrize('continuation',[False,True])
def test_legacy_payload_composed_retry(store,monkeypatch,continuation):
    monkeypatch.setenv('HERMES_HOME',str(store.root.parent))
    d = fixture(store.root.parent,continuation=continuation)
    rc = sg.defect_recheck(d)
    assert rc and sg.recheck_exempts(rc)
    assert sh._detector_exempts_now(d)
    out,filed,left = _retry(store,d)
    assert not filed and not left and out[0]['why'].startswith('detector_exempts_now')


@pytest.mark.parametrize('control',['unread','ambiguous','missing','remote','wrong_fp'])
def test_uncertain_or_real_claim_is_not_exempt(store,monkeypatch,control):
    home=store.root.parent
    monkeypatch.setenv('HERMES_HOME',str(home))
    d=fixture(home,unread=control=='unread',ambiguous=control=='ambiguous')
    if control=='missing': (home/'state.db').unlink()
    if control=='remote': d['host']='remote.example'
    if control=='wrong_fp': d['fingerprint']='agent-bad'
    assert sh._detector_exempts_now(d) is None
    assert len(_retry(store,d)[1])==1


@pytest.mark.parametrize('unread',[False,True])
def test_new_collector_carries_complete_context_through_retry(store,monkeypatch,unread):
    home=store.root.parent
    monkeypatch.setenv('HERMES_HOME',str(home))
    fixture(home,unread=unread)
    log=home/'coach/log.jsonl'
    n,o=map(json.loads,log.read_text().splitlines())
    replay=sg._outcome_recheck_module()
    o['recheck']=replay.capture(n['nudge_id'],o['session_id'],REPLY,
                              ['-> terminal(date)' if unread else '-> terminal('+COMMAND+')'])
    log.write_text('\n'.join(map(json.dumps,[n,o]))+'\n')
    signals=sg.collect_coach(home,{},NOW,n['host'])
    [d]=[sg.to_defect(s) for s in signals if s.get('fp')==sg.fp('agent','pr_state_claim','original',sg.item_key(o['evidence']))]
    assert d['recheck']['reply']==REPLY and len(d['recheck']['reply'])>300
    # Modern replay needs no legacy source: retained evidence travels with its defect.
    log.unlink()
    (home/'state.db').unlink()
    out,filed,left=_retry(store,d)
    assert len(filed)==int(unread) and left==0


def test_capture_never_truncates_or_accepts_incomplete_context():
    replay=sg._outcome_recheck_module()
    assert replay.capture('n','s','x'*(replay.MAX_CONTEXT+1),[]) is None
    assert replay.capture('n','s',REPLY,None) is None
    r=replay.capture('n','s',REPLY,[])
    assert replay.exempts({**r,'complete':False}) is None


@pytest.mark.parametrize('control',['new_turn','missing_result','oversized','conflicting_nudge','conflicting_outcome'])
def test_legacy_evidence_boundaries_stay_open(store,monkeypatch,control):
    import sqlite3
    home=store.root.parent
    monkeypatch.setenv('HERMES_HOME',str(home))
    d=fixture(home)
    if control.startswith('conflicting_'):
        path=home/'coach/log.jsonl'
        records=list(map(json.loads,path.read_text().splitlines()))
        extra=dict(records[0 if control=='conflicting_nudge' else 1], evidence='different claim')
        path.write_text(path.read_text()+json.dumps(extra)+'\n')
    else:
        with closing(sqlite3.connect(home/'state.db')) as db,db:
            if control=='new_turn':
                db.execute('insert into messages values(9,?,?,?,?,?,?)',('original','user','another request',None,None,AT+7))
            elif control=='missing_result':
                db.execute("delete from messages where role='tool'")
            else:
                db.execute("update messages set content=? where role='assistant' and tool_calls is null",('x'*300000,))
    assert sh._detector_exempts_now(d) is None
