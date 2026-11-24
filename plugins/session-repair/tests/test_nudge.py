"""Independent nudge cadence and write/readback boundaries."""
import json
import subprocess
from types import SimpleNamespace
from sr_helpers import PLUGIN, load
nudge = load('_sr_nudge_test', PLUGIN/'nudge.py')
run = load('_sr_nudge_run_test', PLUGIN/'run.py')


def finding():
    return {'id':'BS-long-wait','fingerprint':'BS-long-wait:default/t_123456', 'card':'t_123456','board':'default','chat':'s',
            'evidence':{'kind':'ci','ref':'job','waited_s':3000,'threshold_s':2700,'live_producer':{'state':'pending'}},
            'fix':{'kind':'nudge'},'area':'card'}


def setup(tmp_path, monkeypatch, mode='shadow'):
    sd = tmp_path/'session-repair'; sd.mkdir()
    (sd/'config.json').write_text(json.dumps({'mode':'shadow','nudge_mode':mode}))
    f = finding(); src = SimpleNamespace(open_fix_card=lambda fp: None)
    monkeypatch.setattr(nudge,'fresh',lambda scan, f:(dict(f), src))
    monkeypatch.setattr(nudge,'progress',lambda *a:7)
    monkeypatch.setattr(nudge,'wake_owner',lambda *a:{'outcome':'woken','event_id':19})
    receipts = []
    monkeypatch.setattr(nudge,'receipt',lambda *a:receipts[-1] if receipts else None)
    calls=[]
    def runner(argv, **kw):
        calls.append(argv); receipts.append({'comment_id':11})
        return subprocess.CompletedProcess(argv,0,stdout='',stderr='')
    def tick(now, fs=None):
        return nudge.process(fs or [f],home=tmp_path,now=now,scan=SimpleNamespace(Sources=lambda:src),runner=runner,
                             argv=['hermes'],read_json=run._read_json,write_json=run._write_json,append=run._append)
    return f,tick,calls,sd


def test_renudge_2h01_yes_1h59_no(tmp_path, monkeypatch):
    f,tick,calls,sd=setup(tmp_path,monkeypatch)
    assert len(tick(10000)[0])==1
    assert not tick(10000+7140)[0]
    second=tick(10000+7260)[0][0]
    assert second['nudge_count']==2 and second['earlier_times']==[10000]
    assert not calls


def test_three_unanswered_escalate_not_fourth(tmp_path, monkeypatch):
    f,tick,calls,sd=setup(tmp_path,monkeypatch)
    for now in (10000,17201,24402):
        assert len(tick(now)[0])==1
    actions,escalations,_=tick(31603)
    assert not actions and len(escalations)==1
    assert escalations[0]['fix']['kind']=='card' and escalations[0]['area']=='card'
    assert len(escalations[0]['evidence']['nudge_times'])==3


def test_reply_resets_unanswered_sequence(tmp_path, monkeypatch):
    f,tick,calls,sd=setup(tmp_path,monkeypatch)
    tick(10000)
    monkeypatch.setattr(nudge,'progress',lambda *a:8)
    assert tick(11000)[0][0]['nudge_count']==1


def test_live_exact_comment_readback_and_repeat_message(tmp_path, monkeypatch):
    f,tick,calls,sd=setup(tmp_path,monkeypatch,'live')
    actions,_,_=tick(10000)
    assert actions[0]['receipt']=={'comment_id':11}
    assert calls[0][1:6]==['kanban','--board','default','comment','t_123456']
    tick(17201)
    assert 'Nudge #2' in calls[1][-1] and 'earlier nudges:' in calls[1][-1]


def test_moved_since_scan_no_write(tmp_path, monkeypatch):
    f,tick,calls,sd=setup(tmp_path,monkeypatch,'live')
    monkeypatch.setattr(nudge,'fresh',lambda *a:(None,None))
    assert tick(10000)[0][0]['outcome']=='moved_on'
    assert not calls


def test_unknown_delivery_does_not_resend(tmp_path, monkeypatch):
    f,tick,calls,sd=setup(tmp_path,monkeypatch,'live')
    monkeypatch.setattr(nudge,'receipt',lambda *a:None)
    assert tick(10000)[0][0]['outcome']=='delivery_unknown'
    tick(20000)
    assert len(calls)==1


def test_separate_nudge_caps(tmp_path,monkeypatch):
    f,tick,calls,sd=setup(tmp_path,monkeypatch)
    fs=[{**f,'fingerprint':str(i)} for i in range(120)]
    assert len(tick(10000,fs)[0])==15
    for i in range(1,8): tick(10000+i*900,fs)
    state=json.loads((sd/'nudges-shadow.json').read_text())
    assert len(state['sent'])==100


def test_chat_nudge_uses_resume(tmp_path,monkeypatch):
    f,tick,calls,sd=setup(tmp_path,monkeypatch,'live')
    f['card']=None
    tick(10000)
    assert calls[0][1:6]==['chat','--resume','s','--oneshot','-Q']


def test_modes_are_independent(tmp_path,monkeypatch):
    f,tick,calls,sd=setup(tmp_path,monkeypatch,'live')
    monkeypatch.setattr(run,'load_nudge',lambda:nudge)
    monkeypatch.setattr(run,'load_scan',lambda:SimpleNamespace(Sources=lambda:None))
    monkeypatch.setattr(run,'hermes_argv',lambda:['hermes'])
    fake_runner=lambda argv,**kw: subprocess.CompletedProcess(argv,0,stdout='',stderr='')
    monkeypatch.setattr(nudge,'receipt',lambda *a:{'comment_id':11})
    result=run.tick(home=tmp_path,scanner=lambda:{'findings':[f]},runner=fake_runner,now=10000,run_mode='shadow')
    assert result['nudge_mode']=='live' and result['nudges'][0]['outcome']=='nudged'
    assert result['mode']=='shadow'


def test_detached_chat_admission_does_not_wait_or_kill_owner(tmp_path,monkeypatch):
    f,tick,calls,sd=setup(tmp_path,monkeypatch,'live')
    f['card']=None
    spawned=[]
    def popen(argv,**kw):
        spawned.append((argv,kw))
        return SimpleNamespace(pid=123,poll=lambda:None)
    # Nudge-local binding only: a global Popen patch also captures core's background provider workers.
    monkeypatch.setattr(nudge,'subprocess',SimpleNamespace(run=subprocess.run,Popen=popen,DEVNULL=subprocess.DEVNULL))
    monkeypatch.setattr(nudge,'receipt',lambda *a:{'message_id':12})
    result=nudge.process([f],home=tmp_path,now=10000,scan=SimpleNamespace(Sources=lambda:None),runner=subprocess.run,
                         argv=['hermes'],read_json=run._read_json,write_json=run._write_json,append=run._append)
    assert result[0][0]['receipt']=={'message_id':12}
    assert spawned[0][1]['start_new_session'] is True
    assert 'timeout' not in spawned[0][1]


def test_probe_only_events_are_not_owner_progress(monkeypatch):
    events = [{'id': i+2, 'kind': k, 'payload': {}, 'run_id': None}
              for i,k in enumerate(('external_watch','wake_check_alive','wake_probe_unproven_notice'))]
    events.append({'id':1,'kind':'scheduled','payload':{},'run_id':1})
    scan = SimpleNamespace(_events=lambda *a,**kw: list(reversed(events[:-1])) + events[-1:])
    assert nudge.progress(scan, SimpleNamespace(conn=lambda b:None), finding()) == 1


def test_message_is_plain_and_bounded():
    f = finding()
    f['evidence']['live_producer'] = {'state':{'state':'MERGED','statusCheckRollup':[{'name':'x'*20000}]},'happened':True}
    text = nudge.message(f, [10000,17201])
    assert len(text) <= 850
    assert 'MERGED' in text and 'statusCheckRollup' not in text
    assert '\n' not in text and 'Nudge #3' in text


def test_live_records_wake_receipt(tmp_path, monkeypatch):
    f,tick,calls,sd=setup(tmp_path,monkeypatch,'live')
    wakes=[]
    def wake(scan, src, current, rec, text):
        wakes.append((current,rec,text))
        return {'outcome':'woken','event_id':19,'comment_id':11}
    monkeypatch.setattr(nudge,'wake_owner',wake)
    action=tick(10000)[0][0]
    assert len(wakes)==1 and action['wake']['event_id']==19
    assert json.loads((sd/'nudges-live.json').read_text())['fps'][f['fingerprint']]['wake']['event_id']==19
    assert len(calls[0][-1]) <= 1000


def test_probe_checkpoint_migration_preserves_recent_send(tmp_path,monkeypatch):
    f,tick,calls,sd=setup(tmp_path,monkeypatch)
    tick(10000)
    path=sd/'nudges-shadow.json'
    state=json.loads(path.read_text()); state['fps'][f['fingerprint']]['progress']=999
    path.write_text(json.dumps(state))
    assert not tick(11000)[0]
    assert tick(17201)[0][0]['nudge_count']==2


def test_unknown_wake_recovers_same_identity_without_second_comment(tmp_path,monkeypatch):
    f,tick,calls,sd=setup(tmp_path,monkeypatch,'live')
    monkeypatch.setattr(nudge,'wake_owner',lambda *a:{'outcome':'wake_unknown'})
    tick(10000)
    assert json.loads((sd/'nudges-live.json').read_text())['fps'][f['fingerprint']]['pending']
    monkeypatch.setattr(nudge,'wake_owner',lambda *a:{'outcome':'woken','event_id':19})
    # Recovery must run even when the successful-but-unacknowledged wake removed the finding.
    result=nudge.process([],home=tmp_path,now=11000,scan=SimpleNamespace(Sources=lambda:None),runner=None,
                         argv=[],read_json=run._read_json,write_json=run._write_json,append=run._append)
    assert result[0][0]['recovered'] and result[0][0]['wake']['event_id']==19
    history=json.loads((sd/'nudges-live.json').read_text())['fps'][f['fingerprint']]
    assert history['times']==[10000] and 'pending' not in history and len(calls)==1


def test_installer_changes_only_requested_mode(tmp_path,monkeypatch):
    inst=load('_sr_install_nudge',PLUGIN/'install.py')
    sd=tmp_path/'session-repair'; sd.mkdir()
    cfg=sd/'config.json'; cfg.write_text(json.dumps({'mode':'shadow','nudge_mode':'shadow','other':42}))
    monkeypatch.setattr(inst,'home',lambda:tmp_path)
    monkeypatch.setattr(inst,'plan',lambda h,mode,*a,**kw:[('config',mode)] if mode else [])
    assert inst.main(['--apply','--nudge-mode','live'])==0
    assert json.loads(cfg.read_text())=={'mode':'shadow','nudge_mode':'live','other':42}
    assert inst.main(['--apply','--mode','live'])==0
    assert json.loads(cfg.read_text())=={'mode':'live','nudge_mode':'live','other':42}
