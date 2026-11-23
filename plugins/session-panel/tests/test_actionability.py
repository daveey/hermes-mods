"""Actionability is optional source-derived diagnosis, never panel decision authority."""
import copy
import hashlib
import json
import sqlite3
import sys

import pytest

pytest.importorskip('hermes_cli.kanban_db')
from actionability_fixtures import NOW, bundle, persisted, rows, schedule, view


@pytest.fixture
def state():
    with persisted() as values:
        yield values


def diagnostic(state, *, replica=False, snapshot_at=None):
    return state[2]._detail(state[2].conn(state[4]), state[5], 'default', replica,
                            snapshot_at=snapshot_at)['actionability']


def test_local_reader_uses_real_v1_and_reducer_owner_action(state):
    from hermes_cli.kanban_actionability_sources import describe
    a = diagnostic(state)
    assert a == describe(state[2].conn(state[4]), state[5], now=0)
    row = rows(view(state))[0]
    assert row['actionability'] == a
    tooltip = '\n'.join(row['details'])
    assert 'executor' in tooltip and a['instruction'] in tooltip
    assert 'Actionability' in tooltip and 'unknown' in tooltip


def test_schedule_is_time_neutral_including_action_instruction_and_snapshot(state, monkeypatch):
    from hermes_cli import kanban_actionability_sources as sources
    schedule(state)
    seen = []
    real = sources.describe
    def describe(c, tid, *, now=None):
        seen.append(now)
        return real(c, tid, now=now)
    monkeypatch.setattr(sources, 'describe', describe)
    first = diagnostic(state, snapshot_at=NOW)
    monkeypatch.setattr(sources.time, 'time', lambda: NOW+900)
    later = diagnostic(state, snapshot_at=NOW+900)
    assert seen == [0, 0]
    assert first == later
    assert first['action'] == 'wait_for_named_producer'
    assert first['escalation']['at'] == NOW+700
    assert first['first_observed'] != 0
    assert not any('overdue' in s for s in first['violations'])


def test_blocker_trigger_absolute_deadline_and_receipt(state):
    schedule(state)
    a = diagnostic(state)
    row = rows(view(state))[0]
    text = '\n'.join(row['details'])
    assert a['blocker']['prerequisite'] in text and a['blocker']['trigger'] in text
    assert str(NOW+700) in text and 'receipt unavailable' in text.lower()
    assert 'overdue' not in text.lower()
    overdue = '\n'.join(rows(view(state, now=NOW+701))[0]['details'])
    assert 'overdue' in overdue.lower()
    assert a == diagnostic(state)


def test_missing_deadline_is_never_invented(state):
    row = rows(view(state))[0]
    assert row['actionability']['escalation']['at'] is None
    assert 'deadline unavailable' in '\n'.join(row['details']).lower()


@pytest.mark.parametrize('failure', ['missing_module', 'missing_function', 'exception', 'malformed'])
def test_optional_adapter_failure_never_breaks_hydration(state, monkeypatch, failure):
    from hermes_cli import kanban_actionability_sources as sources
    if failure == 'missing_module':
        monkeypatch.setitem(sys.modules, 'hermes_cli.kanban_actionability_sources', None)
    elif failure == 'missing_function':
        monkeypatch.delattr(sources, 'describe')
    else:
        def fail(*args, **kwargs):
            if failure == 'exception':
                raise RuntimeError('optional diagnostic unavailable')
            return ['not a diagnostic']
        monkeypatch.setattr(sources, 'describe', fail)
    b = bundle(state)
    assert b['cards'][state[5]]['detail']['task']['id'] == state[5]
    row = rows(view(state, b))[0]
    assert row['actionability']['evidence_state'] == 'unknown'
    assert 'unavailable' in '\n'.join(row['details']).lower()


@pytest.mark.parametrize('mutation', ['diagnostic_id','detail_id','board','gateway','profile','owner','version','shape','deadline'])
def test_wrong_identity_or_malformed_diagnostic_is_unavailable(state, mutation):
    b = bundle(state, replica=True)
    rec = b['cards'][state[5]]
    # Baseline must fail for missing carriage, not an import/setup failure.
    assert isinstance(rec['detail'].get('actionability'), dict)
    if mutation == 'diagnostic_id': rec['detail']['actionability']['task_id'] = 't_other'
    if mutation == 'detail_id': rec['detail']['task']['id'] = 't_other'
    if mutation in ('board','gateway','profile'): rec[mutation] = 'other'
    if mutation == 'owner': rec['detail']['owner'] = 'someone-else'
    if mutation == 'version': rec['detail']['actionability']['version'] = 999
    if mutation == 'shape': rec['detail']['actionability'] = {'version':1,'task_id':state[5]}
    if mutation == 'deadline': rec['detail']['actionability']['escalation']['at'] = float('nan')
    row = rows(view(state,b))[0]
    assert row['actionability']['evidence_state'] == 'unknown'
    assert 'unavailable' in '\n'.join(row['details']).lower()
    assert row['gateway'] == 'owner'


def test_replica_and_foreign_unhydrated_remain_unknown(state):
    assert diagnostic(state, replica=True)['evidence_state'] == 'unknown'
    b = bundle(state, replica=True)
    assert rows(view(state,b))[0]['actionability']['evidence_state'] == 'unknown'
    b['cards'] = {}
    row = rows(view(state,b))[0]
    assert row['actionability']['evidence_state'] == 'unknown'
    assert 'unavailable' in '\n'.join(row['details']).lower()


def test_terminal_omitted_and_stale_terminal_na_not_owner_violation(state):
    b = bundle(state)
    b['tasks'][0]['status'] = 'done'
    assert rows(view(state,b)) == []
    b['tasks'][0]['stale'] = True
    row = rows(view(state,b))[0]
    assert 'n/a' in '\n'.join(row['details']).lower()
    assert 'owner_missing' not in '\n'.join(row['details'])


def test_additive_behavior_invariance_including_human_asks(state):
    from test_model import human
    for ask in (False,True):
        b = bundle(state)
        if ask: b['tasks'][0].update(status='blocked',human_request=human(),wait={'wait_state':'human'})
        before = copy.deepcopy(b)
        actual = view(state,b)
        assert b == before
        assert 'actionability' in rows(actual)[0]
        b['cards'][state[5]]['detail'].pop('actionability',None)
        control = view(state,b)
        def strip(m):
            m.pop('revision',None)
            for row in rows(m):
                row.pop('actionability',None)
                row['details'] = [s for s in row['details'] if not s.startswith('Actionability')]
            return m
        assert strip(actual) == strip(control)


def test_demand_bound_one_describe_no_report_no_writes(state, monkeypatch):
    from hermes_cli import kanban_actionability_sources as sources
    api,_,r,w,path,tid = state
    c=r.conn(path)
    statements=[]; calls=[]
    real=sources.describe
    def describe(conn, card, *, now):
        assert conn is c and now == 0
        assert conn.execute('PRAGMA query_only').fetchone()[0] == 1
        calls.append(card)
        return real(conn,card,now=now)
    monkeypatch.setattr(sources,'describe',describe)
    from hermes_cli import kanban_actionability_cli as cli
    import socket
    def forbidden(*args, **kwargs):
        pytest.fail('diagnostic attempted report, network or a new connection')
    monkeypatch.setattr(cli, 'report', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    before='\n'.join(w.iterdump())
    hashes=lambda: [hashlib.sha256(p.read_bytes()).hexdigest() for p in (path,path.with_name(path.name+'-wal')) if p.exists()]
    bytes_before=hashes()
    c.set_trace_callback(statements.append)
    with r.batch([]):
        r.card(tid,'default');r.card(tid,'default')
    c.set_trace_callback(None)
    assert calls == [tid]
    assert len(statements) <= 65, statements  # baseline10 + measured34, with small schema variance
    assert before == '\n'.join(w.iterdump()) and bytes_before == hashes()
    assert not any(s.lstrip().upper().startswith(('INSERT','UPDATE','DELETE','CREATE')) for s in statements)


def test_projection_uses_existing_pinned_sql_snapshot(state):
    r, w, path, tid = state[2:]
    c = r.conn(path)
    c.execute('BEGIN')
    first = diagnostic(state, snapshot_at=NOW)
    w.execute("UPDATE tasks SET assignee='new-owner' WHERE id=?", (tid,))
    assert diagnostic(state, snapshot_at=NOW) == first
    c.rollback()
    assert diagnostic(state, snapshot_at=NOW)['owner']['refs'] == ['new-owner']


@pytest.mark.parametrize('input_name', ['owner_status', 'owner_event', 'owner_pending', 'retry_counter'])
def test_source_named_administrative_owner_inputs(state, monkeypatch, input_name):
    from hermes_cli import kanban_db, kanban_session_activity as ksa
    _, _, r, w, path, tid = state
    owner = kanban_db.create_task(w, title='Administrative owner', assignee='reviewer')
    kanban_db._append_event(w, tid, 'spend_budget_triaged', {'cause': 'scope_stopped', 'episode_uid': 'fixture-stop'})
    kanban_db._append_event(w, tid, 'spend_budget_decision_ready', {'episode': 'fixture-stop', 'producer': {'kind': 'supervisor', 'task_id': owner}})
    b = bundle(state)
    assert b['cards'][tid]['detail']['actionability']['owner']['refs'] == [owner]
    held = {'chat': {'lineage': {'chat'}, 'refs': {tid}, 'bundle': b}}
    monkeypatch.setattr(ksa, 'remote_links_and_origin_rows', lambda **kw: ([], []))
    _, cursor = r.board_changes(None)
    w.execute('ALTER TABLE task_runs ADD COLUMN uid TEXT')
    w.execute('CREATE TABLE IF NOT EXISTS kanban_failure_commands(task_id TEXT,run_uid TEXT,request_uid TEXT,request_sha256 TEXT,operation_kind TEXT,delivery_state TEXT,canonical_receipt_json TEXT,updated_at INTEGER)')
    w.execute("INSERT INTO kanban_failure_commands VALUES (?,'run','request','digest','retain','confirmed',NULL,1)", (owner,))
    held['chat']['bundle'] = bundle(state)
    assert held['chat']['bundle']['cards'][tid]['detail']['actionability']['owner']['refs'] == [owner], held['chat']['bundle']['cards'][tid]['detail']['actionability']
    old = r.external_capture(held)
    _, cursor = r.board_changes(cursor)
    if input_name == 'owner_status':
        w.execute("UPDATE tasks SET status='done' WHERE id=?", (owner,))
    elif input_name == 'owner_event':
        kanban_db._append_event(w, owner, 'stopped_scope_decision', {})
    elif input_name == 'owner_pending':
        w.execute("UPDATE kanban_failure_commands SET delivery_state='pending' WHERE task_id=?", (owner,))
    else:
        w.execute('UPDATE kanban_failure_commands SET updated_at=updated_at+1 WHERE task_id=?', (owner,))
    changed, _ = r.board_changes(cursor)
    new = r.external_capture(held)
    if input_name == 'retry_counter':
        assert old == new, 'nonsemantic retry telemetry must not dirty a diagnostic'
        assert r.diagnostic_stale(held) == set()
    else:
        assert changed.affects(held['chat']) or r.diagnostic_stale(held) == {'chat'}


def test_heartbeat_key_deadline_and_wire_revision_stable(state):
    from hermes_cli import kanban_db
    schedule(state)
    first=diagnostic(state); initial=view(state)
    kanban_db._append_event(state[3],state[5],'heartbeat',{})
    assert first == diagnostic(state)
    assert initial['revision'] == view(state)['revision']


@pytest.mark.parametrize('input_name', ['wake', 'pending', 'sync'])
def test_diagnostic_bootstrap_first_mutation_and_convergence(state, monkeypatch, input_name):
    from hermes_cli import kanban_session_activity as ksa
    _, _, r, w, _, tid = state
    monkeypatch.setattr(ksa, 'remote_links_and_origin_rows', lambda **kw: ([], []))
    empty = {'chat': {'lineage': {'chat'}, 'refs': set()}}
    before = r.external_capture(empty)
    assert r.diagnostic_stale(empty) == set()
    held = {'chat': {'lineage': {'chat'}, 'refs': {tid}, 'bundle': bundle(state)}}
    assert r.external_capture(held) == before
    assert r.diagnostic_stale(held) == set(), 'demand acquisition is not a source write'
    if input_name == 'wake':
        w.execute('INSERT INTO task_wake_checks(task_id,cmd,every,next_at) VALUES (?,?,?,?)',
                  (tid, 'test -e fixture-ready', 120, NOW))
    elif input_name == 'pending':
        w.execute('CREATE TABLE kanban_failure_commands(task_id TEXT,run_uid TEXT,request_uid TEXT,operation_kind TEXT,delivery_state TEXT,canonical_receipt_json TEXT,updated_at INTEGER)')
        w.execute("INSERT INTO kanban_failure_commands VALUES (?,'run','request','retain','pending',NULL,1)", (tid,))
    else:
        w.execute('CREATE TABLE kanban_sync_state(board_id TEXT,mode TEXT)')
        w.execute("INSERT INTO kanban_sync_state VALUES ('fixture','on')")
    assert r.diagnostic_stale(held) == {'chat'}, 'first real write cannot be swallowed as demand seeding'
    held['chat']['bundle'] = bundle(state)
    assert r.diagnostic_stale(held) == set(), 'hydration consumes the change exactly once'
    assert r.external_capture(held) == before, 'diagnostics never alter canonical inventory tuples'
    if input_name == 'pending':
        w.execute('UPDATE kanban_failure_commands SET updated_at=updated_at+1')
        assert r.diagnostic_stale(held) == set(), 'retry telemetry is not semantic change'


def test_diagnostic_signature_and_projection_share_snapshot(state, monkeypatch):
    from hermes_cli import kanban_actionability_sources as sources
    _, _, r, w, _, tid = state
    real = sources.describe
    def racing(c, task_id, *, now):
        result = real(c, task_id, now=now)
        w.execute('INSERT INTO task_wake_checks(task_id,cmd,every,next_at) VALUES (?,?,?,?)',
                  (tid, 'test -e fixture-ready', 120, NOW))
        return result
    monkeypatch.setattr(sources, 'describe', racing)
    held = {'chat': {'lineage': {'chat'}, 'refs': {tid}, 'bundle': bundle(state)}}
    assert r.diagnostic_stale(held) == {'chat'}, 'post-projection write cannot certify stale diagnostic'
    assert not r.conn(state[4]).in_transaction, 'owned snapshot must not leak'


@pytest.mark.parametrize('input_name',['task','event','parent_status','parent_event','run','pending','wake','sync'])
@pytest.mark.parametrize('mode',['full','board_only','catch_up'])
def test_source_mutations_refresh_demanded_inputs(state, monkeypatch, input_name, mode):
    from hermes_cli import kanban_db, kanban_session_activity as ksa, kanban_db_sync as sync
    _,_,r,w,path,tid=state
    parent=kanban_db.create_task(w,title='Prerequisite',assignee='upstream')
    w.execute('INSERT INTO task_links(parent_id,child_id) VALUES (?,?)',(parent,tid))
    b=bundle(state)
    # Parent intentionally absent from hydrated cards (running leaf terminates chain).
    held={'chat':{'lineage':{'chat'},'refs':{tid},'bundle':b}}
    monkeypatch.setattr(ksa,'remote_links_and_origin_rows',lambda **kw:([],[]))
    monkeypatch.setattr(sync,'sync_config',lambda:{})
    r.ksc=type('LocalBoards',(),{'shadow_path':staticmethod(lambda board:path.parent/'absent')})
    _,cursor=r.board_changes(None)
    old=r.external_capture(held)
    old_d=diagnostic(state)
    import dataclasses
    monkeypatch.setattr(r,'lineage',lambda session:['chat'])
    def listed(ids):
        row=dataclasses.asdict(kanban_db.get_task(r.conn(path),tid))
        return [{**row,'gateway':'local','profile':'default','board':'default'}]
    monkeypatch.setattr(r,'linked',listed)
    monkeypatch.setattr(r,'done',lambda ids:[])
    monkeypatch.setattr(r,'panel_inputs',lambda *args:{'gateway':'local','profile':'default'})
    f=state[0]._load_feed().Feed(r,emit=lambda event:None,clock=lambda:NOW)
    base=f.compute('chat')
    if input_name=='task': w.execute('UPDATE tasks SET assignee=? WHERE id=?',('replacement',tid))
    elif input_name in ('event','parent_event'):
        kanban_db._append_event(w,tid if input_name=='event' else parent,'blocked',{'reason':'new source'})
    elif input_name=='parent_status': w.execute("UPDATE tasks SET status='done' WHERE id=?",(parent,))
    elif input_name=='run':
        w.execute("INSERT INTO task_runs(task_id,profile,started_at,status) VALUES (?,?,?,'running')",(tid,'executor',NOW))
    elif input_name=='pending':
        w.execute('CREATE TABLE IF NOT EXISTS kanban_failure_commands(task_id TEXT,run_uid TEXT,request_uid TEXT,operation_kind TEXT,delivery_state TEXT,canonical_receipt_json TEXT)')
        w.execute("INSERT INTO kanban_failure_commands VALUES (?,'run','request','retain','pending',NULL)",(tid,))
    elif input_name=='wake':
        w.execute('INSERT INTO task_wake_checks(task_id,cmd,every,next_at) VALUES (?,?,?,?)',(tid,'test -e fixture-ready',120,NOW))
    elif input_name=='sync':
        w.execute('CREATE TABLE IF NOT EXISTS kanban_sync_state(board_id TEXT,mode TEXT)')
        w.execute("INSERT INTO kanban_sync_state(board_id,mode) VALUES ('fixture','on')")
    changed,_=r.board_changes(cursor)
    new=r.external_capture(held)
    assert changed.affects(held['chat']) or r.diagnostic_stale(held) == {'chat'}, input_name
    fresh=diagnostic(state)
    assert 'actionability' in r.card(tid,'default')['detail']
    # Source mutations need fresh acquisition, not necessarily a changed contract identity.
    from hermes_cli.kanban_actionability_sources import describe
    assert fresh == describe(r.conn(path),tid,now=0)
    if mode=='catch_up':
        captured=f.changes(['chat'])['bundles']['chat']
    else:
        captured=f.compute('chat',base=base if mode=='board_only' else None)
    f.stopped.set()
    assert captured['cards'][tid]['detail']['actionability'] == fresh
    assert rows(captured['model'])[0]['actionability'] == fresh


def test_inactive_reused_snapshot_never_certifies_cached_enrichment(state):
    r=state[2]; cap=r.snapshot_modules();m=cap.snapshot
    source=m.SourceKey('local','default','default','local',str(state[4]));store=m.SnapshotStore();store.add_source(source)
    chat=m.ChatKey('local','default','chat');store.register([m.Watch(chat,'',('chat',),0)])
    def capture(previous=None):
        h=cap.CaptureHints(store.version(source),store.owner_token(source),NOW)
        part=r.capture_local(source,previous,store.demands(source),h)
        store.accept_local(part,h.prior_version)
        return part
    first=capture(); assert first.details
    # Simulate an older captured detail carrying trusted enrichment. A reused
    # cache must discard it even when no tracked task input was dirtied.
    import dataclasses
    first=dataclasses.replace(first,details={key:{**m.thaw(detail),'actionability':diagnostic(state)}
                                          for key,detail in first.details.items()})
    second=capture(first)
    for detail in second.details.values():
        assert not detail.get('actionability') or detail['actionability'].get('evidence_state')=='unknown'
        assert detail.get('actionability') != next(iter(first.details.values())).get('actionability')
