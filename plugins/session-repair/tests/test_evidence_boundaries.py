"""Qualification regressions: evidence boundaries, not elapsed-time stall inference."""
import json
import pytest
pytest.importorskip('hermes_cli.kanban_db')
from sr_helpers import Env, fixtures, load, PLUGIN
F=fixtures()
P=load('_sr_evidence_progress', PLUGIN/'progress.py')

@pytest.fixture
def env(tmp_path,monkeypatch):
    e=Env(tmp_path,monkeypatch)
    monkeypatch.setattr(e.scan_mod,'_process_chats',lambda src:set())
    monkeypatch.setattr(e.scan_mod,'chat_processes',lambda src,chats:{})
    return e

def tick(e,now):
    r=e.scan(now=now,rules=['BS-no-progress'])
    assert not r['errors'],r['errors']
    P.persist(r.pop('_progress_snapshots'),e.home,now)
    return r

def hits(r,tid):
    return [f for f in r['findings'] if f['card']==tid]

@pytest.mark.parametrize('minutes',[31,62])
def test_null_run_heartbeat_uses_exact_current_run_event(env,minutes):
    tid=F.running_quiet(env.conn,env.now,quiet_s=0)
    rid=env.conn.execute('select current_run_id from tasks where id=?',(tid,)).fetchone()[0]
    tick(env,env.now)
    now=env.now+minutes*60
    F.event(env.conn,tid,'heartbeat',None,at=now-12,run_id=rid)
    assert env.conn.execute('select last_heartbeat_at from task_runs where id=?',(rid,)).fetchone()[0] is None
    r=tick(env,now)
    assert not hits(r,tid)
    assert P.load_snapshot(env.home,F.CHAT)['cards'][tid]['prediction']['deferred']=='runtime_activity_unobserved'

@pytest.mark.parametrize('offset,other_run',[(-301,False),(1,False),(-12,True)])
def test_invalid_event_heartbeat_does_not_lend_runtime_liveness(env,offset,other_run):
    tid=F.running_quiet(env.conn,env.now,quiet_s=0)
    rid=env.conn.execute('select current_run_id from tasks where id=?',(tid,)).fetchone()[0]
    tick(env,env.now);now=env.now+31*60
    F.event(env.conn,tid,'heartbeat',None,at=now+offset,run_id=None if other_run else rid)
    assert hits(tick(env,now),tid)

def test_event_heartbeats_do_not_extend_four_hour_ceiling(env):
    tid=F.running_quiet(env.conn,env.now,quiet_s=0)
    rid=env.conn.execute('select current_run_id from tasks where id=?',(tid,)).fetchone()[0]
    tick(env,env.now)
    found=[]
    for minutes in range(15,481,15):
        now=env.now+minutes*60
        F.event(env.conn,tid,'heartbeat',None,at=now-12,run_id=rid)
        fs=hits(tick(env,now),tid)
        if minutes<240: assert not fs
        if minutes==240: assert fs
        assert all(f['variant']=='runtime_unverified' and f['fix']['kind']=='card' for f in fs)
        found+=fs
    assert found

def test_recorded_admission_until_not_generic_two_hour_prediction(env):
    tid=F.card(env.conn,'Ready behind declared admission pacing',status='ready')
    F.age_all_events(env.conn,tid,env.now-3600)
    until=env.now+8*3600
    F.event(env.conn,tid,'chat_wait_unclaimed',{'admission':{'reason':'llm_pacing','status':'held','providers':[{'provider':'p','state':'held','until':until}]}},at=env.now-30)
    tick(env,env.now)
    for m in [31,62,121,240,479]:
        r=tick(env,env.now+m*60)
        assert not hits(r,tid)
    snap=P.load_snapshot(env.home,F.CHAT)
    assert snap['cards'][tid]['prediction']['due_at']>=until
    assert hits(tick(env,until+31*60),tid)

def wait(e,monkeypatch,*,kind='external',probe=True,window=False):
    monkeypatch.setattr(e.scan_mod.Sources,'producer_state',lambda *a:{'state':'not_resolvable','happened':False})
    tid=F.card(e.conn,'Deliberate condition wait',status='scheduled')
    at=int(e.now-4*3600)
    F.age_all_events(e.conn,tid,at)
    aw: dict = {'kind':kind,'ref':'existing producer','why':'Wait for its actual condition; no repeated followup'}
    if window: aw['deploy_window']={'state':'blocked','observed_at':at,'window_at':at,'drain_bound_s':7200}
    payload={'awaiting':aw,'wake_at':e.now+3600}
    if probe:payload['wake_check']='python3 exact_condition.py'
    pid=F.event(e.conn,tid,'scheduled',payload,at=at)
    return tid,pid

def test_reserved_final_condition_never_gets_age_only_owner_nudge(env,monkeypatch):
    from hermes_cli import kanban_continuation as kc
    tid,pid=wait(env,monkeypatch)
    F.event(env.conn,tid,'terminal_run_reserved',{'park_event_id':pid},at=env.now-100)
    assert kc._reserved_for(env.conn,tid,kc.park_state(env.conn,tid))
    assert not env.findings(rule='BS-long-wait',card=tid)

@pytest.mark.parametrize('kind',['external','deploy'])
def test_deliberate_condition_missing_monitor_is_not_producer_stall(env,monkeypatch,kind):
    tid,_=wait(env,monkeypatch,kind=kind)
    fs=env.findings(rule='BS-long-wait',card=tid)
    assert fs and fs[0]['variant']=='producer_unverified'
    assert fs[0]['fix']['kind']=='card'
    assert fs[0]['evidence']['producer_progress']=='unknown'
    assert 'continue' not in fs[0]['fix']['action']

def test_blocked_unprobed_deployment_checkpoint_is_not_early_nudge(env,monkeypatch):
    tid,_=wait(env,monkeypatch,kind='deploy',probe=False,window=True)
    assert not env.findings(rule='BS-long-wait',card=tid)

def test_deliberate_condition_does_not_hide_completion(env,monkeypatch):
    tid,_=wait(env,monkeypatch)
    monkeypatch.setattr(env.scan_mod.Sources,'producer_state',lambda *a:{'state':'done','happened':True,'happened_at':env.now-601})
    assert env.findings(rule='BS-long-wait',card=tid)[0]['variant']=='event_happened'


@pytest.mark.parametrize('change', ['released','other_route','expired','future','prior_episode','newer_skip','missing_reason','unknown_reason'])
def test_admission_observation_requires_current_all_route_hold(env,change):
    tid=F.card(env.conn,'Ready admission control',status='ready')
    F.age_all_events(env.conn,tid,env.now-3600)
    payload={'admission':{'reason':'llm_pacing','status':'held','providers':[{'state':'held','until':env.now+10000}]}}
    at=env.now-30
    if change=='missing_reason':payload['admission'].pop('reason')
    if change=='unknown_reason':payload['admission']['reason']='unknown'
    if change=='released':payload['admission']['status']='eligible'
    if change=='other_route':payload['admission']['providers'].append({'state':'eligible'})
    if change=='expired':payload['admission']['providers'][0]['until']=env.now-1
    if change=='future':at=env.now+1
    if change=='prior_episode':at=env.now-7200
    F.event(env.conn,tid,'chat_wait_unclaimed',payload,at=at)
    if change=='newer_skip':F.event(env.conn,tid,'dispatch_skipped',{'reason':'other','until':env.now-1},at=env.now-10)
    task,c=env.sources().task('default',tid)
    card=env.scan_mod.Card(tid,'default',F.CHAT,task,c,None)
    assert env.scan_mod.ready_admission_until(card,env.now) is None


def test_direct_dispatch_until_preserves_real_claim_deadline(env):
    tid=F.card(env.conn,'Direct paced ready card',status='ready')
    F.age_all_events(env.conn,tid,env.now-3600)
    until=env.now+8*3600
    F.event(env.conn,tid,'dispatch_skipped',{'reason':'llm_pacing','until':until*1000},at=env.now-30)
    tick(env,env.now)
    assert not hits(tick(env,env.now+121*60),tid)
    assert P.load_snapshot(env.home,F.CHAT)['cards'][tid]['prediction']['due_at']==until


@pytest.mark.parametrize('change',['overdue','too_long','missing_stamp','stale_stamp','future_stamp','adverse','completed'])
def test_blocked_deployment_guard_is_bounded_and_not_health(env,monkeypatch,change):
    tid,pid=wait(env,monkeypatch,kind='deploy',probe=False,window=True)
    row=env.conn.execute('select payload from task_events where id=?',(pid,)).fetchone()
    payload=json.loads(row[0])
    at=env.now-4*3600
    if change=='overdue':payload['wake_at']=env.now
    if change=='too_long':payload['wake_at']=at+24*3600+1
    if change=='missing_stamp':payload['awaiting']['deploy_window'].pop('observed_at')
    if change=='stale_stamp':payload['awaiting']['deploy_window']['observed_at']=at-901
    if change=='future_stamp':payload['awaiting']['deploy_window']['observed_at']=at+1
    env.conn.execute('update task_events set payload=? where id=?',(json.dumps(payload),pid));env.conn.commit()
    if change=='adverse':F.event(env.conn,tid,'condition_met',{},at=env.now-10)
    if change=='completed':monkeypatch.setattr(env.scan_mod.Sources,'producer_state',lambda *a:{'state':'done','happened':True,'happened_at':env.now-601})
    assert env.findings(rule='BS-long-wait',card=tid)


def test_stale_reserved_episode_cannot_protect_a_successor_park(env,monkeypatch):
    tid,pid=wait(env,monkeypatch)
    F.event(env.conn,tid,'terminal_run_reserved',{'park_event_id':pid-1},at=env.now-100)
    assert env.findings(rule='BS-long-wait',card=tid)


def test_adverse_evidence_imported_before_older_origin_park_still_counts(env,monkeypatch):
    tid,pid=wait(env,monkeypatch,kind='deploy',probe=False,window=True)
    payload=env.conn.execute('select payload from task_events where id=?',(pid,)).fetchone()[0]
    env.conn.execute('delete from task_events where id=?',(pid,));env.conn.commit()
    adverse=F.event(env.conn,tid,'condition_met',{},at=env.now-10)
    imported=F.event(env.conn,tid,'scheduled',json.loads(payload),at=env.now-4*3600)
    assert adverse<imported
    assert env.findings(rule='BS-long-wait',card=tid)


@pytest.mark.parametrize('change',['valid','url','probe','too_long','no_reason'])
def test_declared_batch_checkpoint_boundary(env,monkeypatch,change):
    tid,pid=wait(env,monkeypatch,kind='deploy',probe=False)
    payload=json.loads(env.conn.execute('select payload from task_events where id=?',(pid,)).fetchone()[0])
    payload['clock_only']=True
    if change=='url':payload['awaiting']['ref']='https://example.test/deployment'
    if change=='probe':payload['wake_check']='python3 exact_condition.py'
    if change=='too_long':payload['wake_at']=int(env.now-4*3600)+8*3600+1
    if change=='no_reason':payload['awaiting']['why']=''
    env.conn.execute('update task_events set payload=? where id=?',(json.dumps(payload),pid));env.conn.commit()
    fs=env.findings(rule='BS-long-wait',card=tid)
    assert bool(fs)==(change!='valid')


def test_event_liveness_ceiling_survives_snapshot_restart(env):
    tid=F.running_quiet(env.conn,env.now,quiet_s=0)
    rid=env.conn.execute('select current_run_id from tasks where id=?',(tid,)).fetchone()[0]
    start=env.now+5*3600
    F.event(env.conn,tid,'heartbeat',None,at=start-12,run_id=rid)
    tick(env,start)  # baseline after a restart cannot re-anchor the source ceiling
    F.event(env.conn,tid,'heartbeat',None,at=start+31*60-12,run_id=rid)
    fs=hits(tick(env,start+31*60),tid)
    assert fs and fs[0]['variant']=='runtime_unverified' and fs[0]['fix']['kind']=='card'
