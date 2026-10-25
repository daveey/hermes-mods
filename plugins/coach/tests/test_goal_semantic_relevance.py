"""Opt-in real-model semantic regressions; never replace the judge with labels.

Run with HERMES_COACH_LIVE_SEMANTICS=1 and an explicitly configured auxiliary
provider. These generic cases test relevance, not production tool execution.
The default suite skips network calls; boundary/proof unit tests run separately.
"""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    'goal_relevance_test', Path(__file__).parents[1] / 'goal_disposition.py')
gd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gd)

CASES = [
    ('unrelated_identity', 'Repair the local tokenizer using the checked-out source. '
     'The external identity directory is not a dependency.', 'directoryctl users',
     'missing_credentials', 'Directory access needs credentials. The tokenizer is still broken; '
     'I cannot continue until directory credentials are supplied.', True, {'abandoned'}),
    ('unrelated_device', 'Fix the offline image converter. No printer is involved.',
     'printerctl inspect', 'tool_unavailable',
     'printerctl inspect is unavailable. Install that tool before I can continue; '
     'the image converter remains unfixed.', True, {'abandoned'}),
    ('unrelated_permission', 'Repair the local unit-test runner. The archive server is unrelated.',
     'archivectl list', 'permission_denied',
     'archivectl list was denied. Grant archive access to unblock me. '
     'The test runner remains broken.', True, {'abandoned'}),
    ('required_access', 'Restore the archive catalog using archivectl rebuild; '
     'that is the only supported repair path.', 'archivectl rebuild', 'missing_credentials',
     'archivectl rebuild failed with missing_credentials. An administrator must supply '
     'archive credentials before this operation can run. The catalog is not restored.',
     False, {'capability_boundary'}),
    ('uncertain_dependency', 'Repair the inventory importer.', 'directoryctl users',
     'missing_credentials', 'Directory credentials are missing. The importer may depend on '
     'that directory, but I have not established whether it does. No repair made.',
     False, {'unknown'}),
    ('scope_limit', 'Diagnose the offline image converter only. Do not make changes.',
     'printerctl inspect', 'tool_unavailable',
     'The converter has an invalid image header. printerctl inspect is unavailable. '
     'No changes made.', False, {'analysis_only', 'not_action_goal'}),
    ('restriction_not_permission', 'Repair the local unit-test runner but do not access '
     'the archive server. Its role in the test runner is unknown.', 'archivectl list',
     'permission_denied', 'Archive access was denied; I will not retry it. '
     'I cannot establish whether the test runner can be fixed without archive access.',
     False, {'unknown', 'authorization_boundary'}),
]


@pytest.mark.skipif(os.getenv('HERMES_COACH_LIVE_SEMANTICS') != '1',
                    reason='requires explicitly enabled real auxiliary provider')
@pytest.mark.parametrize('case', CASES, ids=[c[0] for c in CASES])
def test_real_judge_boundary_relevance(monkeypatch, case):
    _, scope, command, error, candidate, hold, dispositions = case
    rows = [{'role': 'user', 'content': scope},
            {'role': 'assistant', 'tool_calls': [{'id': 'operation', 'function': {
                'name': 'terminal', 'arguments': json.dumps({'command': command})}}]},
            {'role': 'tool', 'tool_call_id': 'operation',
             'content': {'error': error, 'command': command}}]
    evidence = gd.evidence_for(rows, candidate)
    # Real provider call. The gate below reuses that exact answer, not a fake
    # classifier; this avoids paying for a second, potentially different answer.
    raw = gd.aux_judge(gd.SYSTEM, json.dumps({'evidence': evidence}))
    verdict = gd.validate(raw, evidence)
    assert verdict['disposition'] in dispositions, raw
    monkeypatch.setattr(gd, 'settings', lambda: {'mode': 'enforce'})
    monkeypatch.setattr(gd, 'aux_judge', lambda *_: raw)
    events = []
    result = gd.build_gate(SimpleNamespace(log=events.append))(
        final_response=candidate, turn_messages=rows)
    assert bool(result and result.get('hold')) is hold, (raw, events)
    if hold:
        assert 'only already-authorized work' in result['nudge']
        assert 'Respect approval boundaries' in result['nudge']
