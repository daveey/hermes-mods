"""Public structural goldens through the real P1 reducer, never private chat text."""
import importlib.util
import json
import sys
from pathlib import Path

path = Path(__file__).resolve().parents[3] / 'plugins/session-panel/tests/test_model.py'
spec = importlib.util.spec_from_file_location('panel_fixtures', path)
assert spec is not None and spec.loader is not None
f = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)

cases = {
    'agent_held_local': f.held_bundle(),
    'agent_held_replica': f.held_bundle(remote=True),
    'agent_held_spend': f.held_bundle(kind='spend_budget_triaged'),
    'agent_held_human': f.held_bundle(human_request=f.human(), block_kind='needs_input'),
    'canonical_done_with_open_rows': f.bundle(f.task(human_request=f.human(), wait={'wait_state': 'human'}), outcome_json=f.canonical_outcome('done', revision=41)),
    'derived_idle_with_done_history': f.bundle(done=[f.task('t_done', status='done')], outcome_json={'kind': 'done'}),
    'derived_unknown_with_done_history': f.bundle(done=[f.task('t_done', status='done')], input_errors=['user-steps requests unavailable']),
    'own_letters': f.bundle(f.task(human_request=f.human(), wait={'wait_state': 'human'}), requests=[f.request()]),
    'remote_card': f.bundle(f.task(human_request=f.human(), wait={'wait_state': 'human'})),
    'triage': f.bundle(f.task(status='triage', human_request=f.human(), block_kind='needs_input')),
    'blocked_no_request': f.bundle(f.task(block_kind='needs_input')),
    'named_parent': f.bundle(f.task(status='scheduled', wait={'wait_state': 'waiting', 'waiting_on': 'card t_0123456789abcdef', 'waiting_since': f.NOW - 120}),
                             gateway='origin', profile='default', cards={'t_0123456789abcdef': {'board': 'default', 'detail': {'source': 'replica', 'owner': 'remote', 'task': {'id': 't_0123456789abcdef', 'title': 'Publish the release'}}}}),
    'external_watch': f.bundle(f.task(status='scheduled', block_kind='needs_input', human_request=None, wait={'wait_state': 'external_running', 'waiting_on': 'external device log'})),
    'stuck': f.bundle(f.observed(f.task(status='running', activity_at=f.NOW - 8000))),
    'subagents': f.bundle(subagents=[f.observed({'id': 'child', 'title': 'Research'}, 'subagent')]),
    'all_sections': f.bundle(f.task(human_request=f.human(), wait={'wait_state': 'human'}), f.task('t_stuck', status='scheduled', wait={'wait_state': 'stalled'}), f.task('t_ready', status='ready'), f.task('t_wait', status='blocked'), rentals=[{'id': 'box', 'name': 'Machine'}], done=[f.task('t_done', status='done')], outcome_json={'state': 'needs_user'}),
}
for state in ('dismissed', 'answered', 'withdrawn', 'open'):
    cases['shared_human_' + state] = f.shared_human_bundle(state)
parent, leaf = 't_1111111111111111', 't_2222222222222222'
for name in ('agent_held_local', 'agent_held_replica', 'agent_held_spend'):
    cases[name]['cards']['t_abc123']['detail']['events'][0]['created_at'] = f.NOW - (5 * 3600 + 20 * 60)
for kind, on in [('ci', 'CI job 42'), ('deploy', 'deploy release 42'), ('clock', 'the clock until 2026-11-25 12:00 UTC')]:
    b = f.held_bundle()
    b['tasks'][0]['wait']['waiting_on'] = on
    cases['producer_' + kind] = b
cases['leaf_wait_with_running'] = f.bundle(
    f.task(status='todo', upstream={'id': parent, 'title': 'Intermediate', 'gateway': 'remote', 'profile': 'default'}),
    f.observed(f.task('t_running', status='running', title='Active portable work')),
    cards={parent: {'detail': {'owner': 'remote', 'task': {'id': parent, 'title': 'Intermediate', 'status': 'todo'},
                              'links': {'parents': [leaf]}}},
           leaf: {'detail': {'owner': 'worker-one', 'task': {'id': leaf, 'title': 'Reconcile cutover', 'status': 'scheduled',
                                                           'hostname': 'worker-one-host'},
                             'events': [{'kind': 'scheduled', 'payload': {'awaiting': {
                                 'kind': 'chat', 'why': 'Choose which premise to retain'}}}]}}})
bound = f.request()
bound['owner'] = {**bound['owner'], 'gatewayId': 'install:fixture-owner'}
bound['scope'] = {**bound['scope'], 'complete': True}
for choice in bound['choices']:
    choice['effect'] = 'answer'
cases['bound_source'] = f.bundle(requests=[bound], gateway='source-gateway', gateway_aliases=['install:fixture-owner'])
cases['bound_foreign'] = f.bundle(requests=[bound], gateway='other-gateway', gateway_aliases=['install:other'])
cases['bound_partial_identity'] = f.bundle(requests=[bound], gateway='source-gateway', gateway_aliases=[])
approval = f.request()
approval.update(kind='approval', limits=[{'key': 'Runtime', 'value': 'LIMIT-4-HOURS', 'permissionRequired': True}],
                exclusions=['EXCLUDED-SPEND'], outcomes={'approve': 'Stage A only', 'decline': 'No execution'})
approval['scope'] = {**approval['scope'], 'complete': True, 'markdown': 'CANONICAL-PLAN distinct from the card brief'}
approval['choices'][0].update(effect='approve', explain='Only the bounded stage is authorized')
cases['approval_boundaries'] = f.bundle(f.task(human_request=f.human(), wait={'wait_state': 'human'}), requests=[approval])
for departed in (False, True):
    for unconfirmed in (False, True):
        r = f.request(state='cancelled')
        r.update(resolution={'operationId': 'retiring-op', 'choiceId': 'card-0', 'revision': 3},
                 delivery={'state': 'delivering', 'unconfirmed': unconfirmed,
                           'text': 'Delivery to remote not confirmed (120 min)' if unconfirmed else 'Delivering…'})
        cases[f'retiring_{departed}_{unconfirmed}'] = f.bundle(
            *([] if departed else [f.task(human_request=f.human(), wait={'wait_state': 'human'})]), requests=[r])
for name, row in f.retiring_owner_cases().items():
    for unconfirmed in (False, True):
        r = f.request(state='cancelled')
        r.update(resolution={'operationId': 'retiring-op', 'choiceId': 'card-0', 'revision': 3},
                 delivery={'state': 'delivering', 'text': 'Delivery to remote not confirmed (120 min)' if unconfirmed else 'Delivering…'})
        cases[f'owner_delivery_{name}_{unconfirmed}'] = f.bundle(row, requests=[r])
        cases[f'owner_baseline_{name}'] = f.bundle(row)
cases['relative_clock'] = f.bundle(f.observed(f.task('t_clock', status='running',
    current_run_started_at=f.NOW - 36 * 60, activity_at=f.NOW - 60,
    run_machine='worker', run_model='example-model', activity_kind='tool')))
cases['elapsed_layout'] = f.bundle(f.observed(f.task('t_elapsed', status='running',
    title='Restore disappearing elapsed runtime readouts in a long task title',
    current_run_started_at=f.NOW - 7 * 60, activity_at=f.NOW - 10,
    run_machine='portable-worker-with-a-long-qualified-location',
    run_model='example-model-with-a-long-qualified-version')))
decided = f.request(state='resolved')
decided['resolution'] = {'operationId': 'instant-op', 'choiceId': 'card-0', 'revision': 3}
cases['instant_decision'] = f.bundle(f.task(human_request=f.human(), wait={'wait_state': 'human'}), requests=[decided])
for name, kind, reason in [('source_unavailable', 'unresolved', 'source_unknown'),
                           ('own_turn_unknown_remote', 'running', None),
                           ('genuine_interruption', 'unresolved', 'interrupted')]:
    outcome = f.canonical_outcome(kind)
    if reason:
        outcome['status']['reason'] = reason
    outcome['status']['presentation'] = {'stripe': 'none' if reason == 'source_unknown' else ('waiting' if kind == 'running' else 'attention'),
                                         'reason_codes': ['own_turn_active' if kind == 'running' else reason]}
    if reason == 'source_unknown':
        outcome['status']['coverage'] = {'state': 'unknown', 'reasons': ['cards:card_read_failed:LinksUnavailable']}
    cases[name] = f.bundle(f.task(status='running', stale=True), outcome_json=outcome)
sys.path.insert(0, str(path.parent))
from test_hierarchy import outline_case
cases.update({
    'tree_direct': outline_case({'t_a': ['t_b']}),
    'tree_multihop': outline_case({'t_a': ['t_b'], 't_b': ['t_c']}, displayed=['t_a', 't_c']),
    'tree_cross_section': outline_case({'t_a': ['t_b']}, running=['t_b']),
    'tree_group': outline_case({'t_a': ['t_f'], 't_b': ['t_f'], 't_c': ['t_f']}, displayed=['t_a', 't_b', 't_c']),
    'tree_single': outline_case({'t_a': ['t_f']}, displayed=['t_a']),
    'tree_cycle': outline_case({'t_a': ['t_b'], 't_b': ['t_a']}),
    'tree_depth': outline_case({f't_{i:x}': [f't_{i+1:x}'] for i in range(10)}),
})
for kind, on in [('ci', 'ci example/repo'), ('deploy', 'deploy example-service'), ('clock', 'the clock until 2026-11-24 14:30 UTC')]:
    for suffix, expected, waited in [('normal', 1500, 720), ('over', 1500, 2460), ('unknown', None, 720)]:
        cases[f'eta_{kind}_{suffix}'] = f.bundle(f.task(status='scheduled', wait={
            'wait_state': 'external_running', 'waiting_on': on, 'waiting_since': f.NOW - waited,
            'waited_since': f.NOW - waited, 'expected_s': expected, 'estimate_source': 'fixture history'}))
# The reported row: a scheduled park on a hub core deploy whose external watch is unread (core external_unknown).
DEPLOY_UNREAD_WAIT = {'wait_state': 'external_unknown', 'wait_kind': 'deploy',
                      'waiting_on': 'deploy core 0000000 → example/core mirror main (hub core deploy)',
                      'waiting_since': f.NOW - 600, 'external': {'health': 'ok', 'job': 'unknown'}}
cases['eta_deploy_unread'] = f.bundle(f.task(status='scheduled', wait=DEPLOY_UNREAD_WAIT))
for _name, _on in (('clock_reason', 'the clock until 2027-01-15 08:01 UTC (Event watch on service owners; backstop at nightly rollout 10:30Z)'),
                   ('clock_unreasoned', 'the clock until 2027-01-15 08:01 UTC')):
    cases[_name] = f.bundle(f.task(status='scheduled', wait={'wait_state': 'waiting', 'waiting_on': _on, 'waiting_since': f.NOW - 300}))
cases['clock_probe'] = f.bundle(f.task(status='scheduled', hostname='long-worker.example.com', wait={
    'wait_state': 'waiting', 'waiting_on': 'the clock until 2027-01-15 08:01 UTC',
    'waiting_since': f.NOW - 300, 'prerequisite': 'Service rollout verification completes',
    'watching': ['t_0000000000000001', 't_0000000000000002'],
    'next_actor': 'cards t_0000000000000001, t_0000000000000002 (event watch)',
    'backstop_at': f.NOW + 600}))
cases['aged_parked'] = f.bundle(f.task(status='scheduled', wait={
    'wait_state': 'external_running', 'waiting_on': 'ci job 42', 'waiting_since': f.NOW - 300}),
    computed_at=f.NOW - 120)
# One wait colour category: the reported stale CI wait (synced 58 min ago on its owner), and a mixed control.
PALETTE_CI = {'wait_state': 'external_running', 'waiting_on': 'ci example/repo PR #751', 'waiting_since': f.NOW - 300}
cases['palette_ci_fresh'] = f.bundle(f.task(status='scheduled', wait=dict(PALETTE_CI)))
cases['palette_ci_stale_replica'] = f.bundle(f.task(status='scheduled', wait=dict(PALETTE_CI), remote=True,
                                                    source='replica', stale=True, synced_at=f.NOW - 3480))
cases['palette_mixed'] = f.bundle(f.task('t_ci', status='scheduled', wait=dict(PALETTE_CI), stale=True),
                                  f.task('t_plain', status='blocked'))
# User screenshot shape: two uncategorised waits and one CI wait (fresh), in that display order.
cases['palette_two_amber_one_blue'] = f.bundle(f.task('t_amber1', status='blocked'),
                                               f.task('t_blue', status='scheduled', wait=dict(PALETTE_CI)),
                                               f.task('t_amber2', status='blocked'))
from test_wait_palette import stalled_cases, withdrawal_cases
for name, b in stalled_cases():
    if not name.endswith('triage') or f.AGED:
        cases['palette_stuck_' + name] = b
for name, b, _ in withdrawal_cases():
    cases['palette_' + name] = b
# Composed owned-board fixture: real CoreReaders adapter, not a UI envelope.
import os
import tempfile
from unittest.mock import patch
try:
    from hermes_cli import kanban_db, kanban_db_connect
    import hermes_cli.kanban_triage_attention
except ImportError:
    pass
else:
    api_spec = importlib.util.spec_from_file_location('_triage_fixture_api', path.parent.parent / 'dashboard/plugin_api.py')
    api = importlib.util.module_from_spec(api_spec)
    sys.modules[api_spec.name] = api
    api_spec.loader.exec_module(api)
    with tempfile.TemporaryDirectory(prefix='triage-panel-fixture-', dir=os.environ.get('TMPDIR')) as home:
        board_path = Path(home) / 'kanban.db'
        # Preserve descendant fences; drop only the inherited live-board selection.
        environment = patch.dict(os.environ, {**{k: v for k, v in os.environ.items()
            if k not in ('HERMES_KANBAN_DB', 'HERMES_KANBAN_BOARD', 'HERMES_KANBAN_HOME')},
            'HERMES_HOME': home}, clear=True)
        environment.start()
        conn = kanban_db_connect.connect(board_path)
        reader = api.CoreReaders(state_db=Path(home) / 'state.db')
        reader.boards = lambda: [('default', board_path)]
        try:
            def backdate_creation(conn, tid, at):
                # create_task records its history at wall-clock time, after the fixture's fixed clock. Core orders
                # lifecycle events by recorded time, so keep the fixture chronology consistent: created before entry.
                conn.execute('UPDATE task_events SET created_at=? WHERE task_id=?', (at, tid))
                conn.execute('UPDATE tasks SET created_at=? WHERE id=?', (at, tid))
            for age in (599, 600, 601, 6 * 86400):
                tid = kanban_db.create_task(conn, title='Owned triage attention fixture', initial_status='running')
                conn.execute("UPDATE tasks SET status='triage' WHERE id=?", (tid,))
                backdate_creation(conn, tid, f.NOW - age - 100)
                conn.execute("INSERT INTO task_events(task_id,kind,created_at,payload) VALUES (?,'decision_retained',?,'{}')", (tid, f.NOW - age))
                # Push the entry outside the bounded display tail.
                for _ in range(45):
                    conn.execute("INSERT INTO task_events(task_id,kind,created_at,payload) VALUES (?,'decision_retained',?,'{}')", (tid, f.NOW - 1))
                conn.commit()
                record = reader.card(tid, 'default')
                assert len(record['detail']['events']) == api.EVENTS_KEPT
                assert record['detail']['task']['triage_entered_at'] == f.NOW - age
                row = {**record['detail']['task'], 'gateway': 'fixture-gateway', 'profile': 'default', 'board': 'default'}
                cases['composed_triage_' + str(age)] = f.bundle(row, cards={tid: record}, outcome_json=f.canonical_outcome('waiting'))
                # Same real triage card beside a live CI wait: header squares must follow each row's box colour.
                ci = f.task('t_ci0001', status='scheduled', gateway='fixture-gateway', wait=dict(PALETTE_CI))
                cases['composed_triage_ci_' + str(age)] = f.bundle(row, ci, cards={tid: record}, outcome_json=f.canonical_outcome('waiting'))
            # Shared-blocker group: the triage parent is read through the same real
            # adapter, but only its dependants are session rows (the group owns the red).
            for age in (599, 601):
                tid = kanban_db.create_task(conn, title='Owned triage shared stage', initial_status='running')
                conn.execute("UPDATE tasks SET status='triage' WHERE id=?", (tid,))
                backdate_creation(conn, tid, f.NOW - age - 100)
                conn.execute("INSERT INTO task_events(task_id,kind,created_at,payload) VALUES (?,'decision_retained',?,'{}')", (tid, f.NOW - age))
                conn.commit()
                record = reader.card(tid, 'default')
                assert record['detail']['task']['triage_entered_at'] == f.NOW - age
                parent = {**record['detail']['task'], 'gateway': 'fixture-gateway', 'profile': 'default', 'board': 'default'}
                children = [f.task(cid, status='todo', gateway='fixture-gateway', upstream=parent) for cid in ('t_111111', 't_222222')]
                cases['composed_triage_group_' + str(age)] = f.bundle(*children, cards={tid: record}, outcome_json=f.canonical_outcome('waiting'))
            # An explicitly stopped, retained scope (core annotates ``retained_stop`` from the same board
            # snapshot) ages like every other triage cause; the stop itself stays a non-executing hold.
            for age in (600, 601, 6 * 86400):
                tid = kanban_db.create_task(conn, title='Owned stopped retained fixture', initial_status='running')
                conn.execute("UPDATE tasks SET status='triage' WHERE id=?", (tid,))
                backdate_creation(conn, tid, f.NOW - age - 100)
                conn.execute("INSERT INTO task_events(task_id,kind,created_at,payload) VALUES (?,'decision_retained',?,?)",
                             (tid, f.NOW - age, json.dumps({'disposition': {'reason': 'scope_stopped', 'summary': 'acceptance unmet'}})))
                conn.commit()
                record = reader.card(tid, 'default')
                stop = record['detail']['task'].get('retained_stop')
                if stop is None:
                    break  # a core without the retained-stop receipt cannot compose this case: skipped, not faked
                assert stop['reason'] == 'scope_stopped'
                assert record['detail']['task']['triage_entered_at'] == f.NOW - age
                assert not record['detail']['task'].get('human_request')
                row = {**record['detail']['task'], 'gateway': 'fixture-gateway', 'profile': 'default', 'board': 'default'}
                cases['composed_triage_retained_' + str(age)] = f.bundle(row, cards={tid: record}, outcome_json=f.canonical_outcome('waiting'))
        finally:
            reader.release()
            conn.close()
            environment.stop()
# Timing goldens cross the real final producer boundary. A reducer-only model
# has no source TTL and cannot prove source expiry independently of heartbeats.
feed_path = path.parent.parent / 'dashboard' / 'feed.py'
feed_spec = importlib.util.spec_from_file_location('_timing_fixture_feed', feed_path)
assert feed_spec is not None and feed_spec.loader is not None
feed_module = importlib.util.module_from_spec(feed_spec)
sys.modules[feed_spec.name] = feed_module
feed_spec.loader.exec_module(feed_module)
producer = feed_module.Feed(None, emit=lambda _: None, clock=lambda: f.NOW, full_every_s=60)
fixtures = {}
for name, b in cases.items():
    timing = name in ('relative_clock', 'elapsed_layout', 'aged_parked', 'clock_reason', 'clock_unreasoned') or name.startswith(('agent_held_', 'eta_'))
    model = f.model.reduce_panel(b, now=f.NOW, projector=f.project, wire=timing)
    if name == 'elapsed_layout' or name.startswith('eta_'):
        model = producer._client_bundle(name, {**b, 'model': model}, True)['model']
    fixtures[name] = {'model': model, 'now': f.NOW}
fixtures['eta_deploy_unread']['wait'] = DEPLOY_UNREAD_WAIT
producer.stopped.set()
print(json.dumps({**fixtures,
    **{'header_clock_' + name: case for name, case in f.header_clock_cases().items()}}))
