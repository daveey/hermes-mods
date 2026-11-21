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
    'external_watch': f.bundle(f.task(status='scheduled', block_kind='needs_input', human_request=None, wait={'wait_state': 'external_running', 'waiting_on': 'external device log'})),
    'stuck': f.bundle(f.observed(f.task(status='running', activity_at=f.NOW - 8000))),
    'subagents': f.bundle(subagents=[f.observed({'id': 'child', 'title': 'Research'}, 'subagent')]),
    'all_sections': f.bundle(f.task(human_request=f.human(), wait={'wait_state': 'human'}), f.task('t_stuck', status='scheduled', wait={'wait_state': 'stalled'}), f.task('t_ready', status='ready'), f.task('t_wait', status='blocked'), rentals=[{'id': 'box', 'name': 'Machine'}], done=[f.task('t_done', status='done')], outcome_json={'state': 'needs_user'}),
}
print(json.dumps({name: {'model': f.model.reduce_panel(b, now=f.NOW, projector=f.project)} for name, b in cases.items()}))
