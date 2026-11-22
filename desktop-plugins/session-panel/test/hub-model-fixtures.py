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
print(json.dumps({name: {'model': f.model.reduce_panel(b, now=f.NOW, projector=f.project)} for name, b in cases.items()}))
