"""Public structural goldens through the real P1 reducer, never private chat text."""
import importlib.util
import json
from pathlib import Path

path = Path(__file__).resolve().parents[3] / 'plugins/session-panel/tests/test_model.py'
spec = importlib.util.spec_from_file_location('panel_fixtures', path)
assert spec is not None and spec.loader is not None
f = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)

cases = {
    'canonical_done_with_open_rows': f.bundle(f.task(human_request=f.human(), wait={'wait_state': 'human'}), outcome_json=f.canonical_outcome('done', revision=41)),
    'canonical_unknown_with_done_history': f.bundle(done=[f.task('t_done', status='done')], outcome_json={'kind': 'done'}),
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
parent, leaf = 't_1111111111111111', 't_2222222222222222'
cases['leaf_wait_with_running'] = f.bundle(
    f.task(status='todo', upstream={'id': parent, 'title': 'Intermediate', 'gateway': 'remote', 'profile': 'default'}),
    f.observed(f.task('t_running', status='running', title='Active portable work')),
    cards={parent: {'detail': {'owner': 'remote', 'task': {'id': parent, 'title': 'Intermediate', 'status': 'todo'},
                              'links': {'parents': [leaf]}}},
           leaf: {'detail': {'owner': 'worker-one', 'task': {'id': leaf, 'title': 'Reconcile cutover', 'status': 'scheduled',
                                                           'hostname': 'worker-one-host'},
                             'events': [{'kind': 'scheduled', 'payload': {'awaiting': {
                                 'kind': 'chat', 'why': 'Choose which premise to retain'}}}]}}})
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
print(json.dumps({name: {'model': f.model.reduce_panel(b, now=f.NOW, projector=f.project)} for name, b in cases.items()}))
