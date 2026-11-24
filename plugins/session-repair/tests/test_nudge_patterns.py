"""Boundary and safety coverage for the two nudge detectors."""
import json
import pytest
pytest.importorskip('hermes_cli.kanban_db')
from sr_helpers import Env, fixtures
F = fixtures()

@pytest.fixture
def env(tmp_path, monkeypatch):
    e = Env(tmp_path, monkeypatch)
    monkeypatch.setattr(e.scan_mod.Sources, 'producer_state', lambda self, kind, ref: {'state': 'pending', 'happened': False})
    monkeypatch.setattr(e.scan_mod.Sources, 'outcome_rows', lambda self: [])
    return e

def wait(env, age=3000, kind='ci', ref='https://github.com/example/project/actions/runs/123', status='scheduled'):
    tid = F.card(env.conn, 'Waiting for producer', status=status)
    F.age_all_events(env.conn, tid, env.now-age)
    F.event(env.conn, tid, 'scheduled', {'awaiting': {'kind': kind, 'ref': ref}, 'reason': 'producer', 'wake_at': env.now+86400}, at=env.now-age)
    return tid

def hits(env, tid):
    return env.findings(rule='BS-long-wait', card=tid)

@pytest.mark.parametrize('age,expected', [(3000,1),(1800,0)])
def test_ci_age(env, age, expected):
    result = hits(env, wait(env, age))
    assert len(result) == expected
    if result:
        assert result[0]['fix']['kind'] == 'nudge'
        assert result[0]['evidence']['threshold_s'] == 2700

def test_completed_green_immediate(env, monkeypatch):
    monkeypatch.setattr(env.scan_mod.Sources, 'producer_state', lambda *a: {'state':'completed green','happened':True,'happened_at':env.now-601})
    assert hits(env, wait(env, 300))[0]['variant'] == 'event_happened'

def test_human_and_running_excluded(env):
    tid = wait(env, status='blocked')
    env.conn.execute('UPDATE tasks SET human_request=?, block_kind=? WHERE id=?', (json.dumps(F.VALID_ASK),'needs_input',tid))
    env.conn.commit()
    assert not hits(env, tid)
    assert not hits(env, wait(env, status='running'))

def test_live_run_even_on_scheduled_card_excluded(env):
    tid = wait(env)
    F._run(env.conn, tid, env.now, started_s=100)
    assert not hits(env, tid)

def test_latest_park_and_run_boundary(env):
    tid = wait(env, age=10000)
    F.event(env.conn, tid, 'scheduled', {'awaiting':{'kind':'ci','ref':'job'}}, at=env.now-60)
    assert not hits(env, tid)
    tid = wait(env, age=10000)
    F._run(env.conn, tid, env.now, started_s=100, status='completed')
    assert not hits(env, tid)

def test_clock_never_and_resource_threshold(env):
    assert not hits(env, wait(env, 99999, 'clock'))
    assert hits(env, wait(env, 7201, 'resource'))
    assert not hits(env, wait(env, 7199, 'resource'))

def test_card_requires_quiet_producer(env):
    producer = F.card(env.conn, 'Producer', status='ready', session=None)
    tid = wait(env, 14401, 'card', producer)
    assert not hits(env, tid)
    F.age_all_events(env.conn, producer, env.now-3601)
    assert hits(env, tid)

def test_failed_readback_still_ages(env, monkeypatch):
    monkeypatch.setattr(env.scan_mod.Sources, 'producer_state', lambda *a: {'state':'readback_failed','happened':False})
    assert hits(env, wait(env))[0]['evidence']['live_producer']['state'] == 'readback_failed'

def quiet(env, monkeypatch, sid='quiet', age=3660):
    env.state.execute('INSERT INTO sessions (id,title,started_at,outcome_json) VALUES (?,?,?,?)',(sid,'Session repair' if sid=='repair' else 'Quiet',env.now-7200,json.dumps({'kind':'waiting','on':[],'items':[]})))
    env.state.execute('INSERT INTO messages (session_id,role,content,timestamp) VALUES (?,?,?,?)',(sid,'assistant','Waiting',env.now-age))
    env.state.commit()
    monkeypatch.setattr(env.scan_mod.Sources, 'outcome_rows', lambda self: [{'id':sid,'kind':'waiting','on':[],'items':[]}])
    monkeypatch.setattr(env.scan_mod.Sources, 'chat_obligations', lambda self,sid: False)
    return env.findings(rule='BS-quiet-chat')

def test_quiet_chat_61_minutes(env, monkeypatch):
    f = quiet(env, monkeypatch)
    assert len(f)==1 and f[0]['fix']['kind']=='nudge' and f[0]['chat']=='quiet'

def test_quiet_exact_target_revalidation_uses_stored_outcome(env, monkeypatch):
    from sr_helpers import PLUGIN, load
    n = load('_sr_quiet_revalidation', PLUGIN/'nudge.py')
    f = quiet(env, monkeypatch)[0]
    source = env.sources()
    monkeypatch.setattr(env.scan_mod.Sources, 'outcome_rows', lambda *a: pytest.fail('must not re-audit all chats'))
    monkeypatch.setattr(env.scan_mod, 'Sources', lambda: source)
    current, _ = n.fresh(env.scan_mod, f)
    assert current is not None
    env.state.execute('UPDATE sessions SET outcome_json=? WHERE id=?',(json.dumps({'kind':'resolved'}),'quiet'))
    env.state.commit()
    assert n.fresh(env.scan_mod, f)[0] is None


def test_quiet_repair_chat_excluded(env, monkeypatch):
    assert not quiet(env, monkeypatch, 'repair')

def test_quiet_recent_chat_excluded(env, monkeypatch):
    assert not quiet(env, monkeypatch, age=3540)

def test_quiet_linked_card_excluded(env, monkeypatch):
    F.card(env.conn, 'Work', status='scheduled', session='quiet')
    assert not quiet(env, monkeypatch)


def test_real_revalidation_rejects_moved_card(env, monkeypatch):
    from sr_helpers import PLUGIN, load
    n = load('_sr_revalidation', PLUGIN/'nudge.py')
    tid = wait(env)
    f = hits(env, tid)[0]
    source = env.sources()
    monkeypatch.setattr(env.scan_mod, 'Sources', lambda: source)
    current, _ = n.fresh(env.scan_mod, f)
    assert current is not None
    F.event(env.conn, tid, 'commented', {'author':'owner'}, at=env.now)
    current, _ = n.fresh(env.scan_mod, f)
    assert current is None


def test_quiet_muted_archived_cli_and_open_ask_excluded(env, monkeypatch):
    assert quiet(env, monkeypatch)
    for field,value in [('muted',1),('archived',1),('source','cli')]:
        env.state.execute(f'UPDATE sessions SET {field}=? WHERE id=?',(value,'quiet'))
        env.state.commit()
        assert not env.findings(rule='BS-quiet-chat')
        env.state.execute(f'UPDATE sessions SET {field}=? WHERE id=?',('desktop' if field=='source' else 0,'quiet'))
        env.state.commit()
    monkeypatch.setattr(env.scan_mod.Sources,'chat_obligations',lambda *a:True)
    assert not env.findings(rule='BS-quiet-chat')
