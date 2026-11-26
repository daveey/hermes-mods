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
print(json.dumps({**{name: {'model': f.model.reduce_panel(b, now=f.NOW, projector=f.project,
    wire=name in ('relative_clock', 'elapsed_layout', 'aged_parked', 'clock_reason', 'clock_unreasoned') or name.startswith(('agent_held_', 'eta_'))), 'now': f.NOW} for name, b in cases.items()},
    **{'header_clock_' + name: case for name, case in f.header_clock_cases().items()}}))
