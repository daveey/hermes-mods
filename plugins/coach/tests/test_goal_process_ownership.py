"""Process evidence cannot certify ownership without a native consumer contract.

These are conservative boundary regressions, not a positive process adapter.
No process is spawned, polled, adopted, signalled, or drained.
"""
import importlib.util
import json
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    'process_goal_boundary', Path(__file__).parents[1] / 'goal_disposition.py')
gd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gd)


@pytest.mark.parametrize('ref', [
    {'process_id': 'proc_0123456789ab'},
    {'gateway': socket.gethostname(), 'profile': 'default',
     'process_id': 'proc_0123456789ab'},
    {'gateway': socket.gethostname(), 'profile': 'default',
     'board': 'default', 'task_id': 'proc_0123456789ab'},
    {'gateway': socket.gethostname(), 'profile': 'default',
     'process_id': '0123456789ab'},
])
def test_native_handle_and_notify_do_not_certify_return(tmp_path, monkeypatch, ref):
    from tools.process_registry import ProcessSession
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    # Native objects know a spawning conversation, not an authoritative goal
    # binding or successful result-consumer poll for this handle.
    process = ProcessSession(id='proc_0123456789ab', command='repair example app',
                             parent_session_id='origin', session_key='origin',
                             notify_on_complete=True)
    monkeypatch.setattr(gd._ownership, '_ro_connect',
                        lambda *_: pytest.fail('unsupported process read a task database'))
    evidence = gd.evidence_for([
        {'role': 'user', 'content': 'Repair example app'},
        {'role': 'assistant', 'tool_calls': [{'id': 'spawn', 'function': {
            'name': 'terminal', 'arguments': json.dumps({
                'command': process.command, 'background': True, 'notify': True})}}]},
        {'role': 'tool', 'tool_call_id': 'spawn', 'content': {
            'session_id': process.id, 'status': 'running',
            'notify_on_complete': process.notify_on_complete,
            'parent_session_id': process.parent_session_id,
            'consumer_live': True, 'goal': 'Repair example app'}}
    ], 'The process owns the repair and will return its result.')
    verdict = dict(goal='Repair example app', actionable=True, unfinished=True,
                   disposition='owned_continuing', evidence_refs=list(evidence),
                   boundary_kind=None, continuation_ref=ref, confidence=.99)
    with pytest.raises(ValueError, match='owner_readback_unavailable'):
        gd.validate_disposition(verdict, evidence, session_id='origin')
    assert list(tmp_path.iterdir()) == []


def test_process_unknown_does_not_release_prior_hold(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(gd, 'settings', lambda: {'mode': 'enforce'})
    monkeypatch.setattr(gd, '_profile_key', lambda: (str(tmp_path), 'default'))
    monkeypatch.setattr(gd._scope, 'resolve_scope', lambda *a: [
        {'role': 'user', 'content': 'Repair example app'}])
    verdict = dict(goal='Repair example app', actionable=True, unfinished=True,
                   disposition='owned_continuing', evidence_refs=['user:0', 'candidate'],
                   boundary_kind=None, continuation_ref={'process_id': 'proc_0123456789ab'},
                   confidence=.99)
    monkeypatch.setattr(gd, 'aux_judge', lambda *a: verdict)
    events = []
    gate = gd.build_gate(SimpleNamespace(log=events.append))
    # Initial UNKNOWN remains fail-open under the existing public contract.
    assert gate(session_id='origin', final_response='A process owns this.') is None
    assert events[-1]['event'] == 'goal_disposition_unknown'
    # Core must retain the already-confirmed unresolved fallback on recheck.
    with pytest.raises(ValueError, match='owner_readback_unavailable'):
        gate(session_id='origin', final_response='A process owns this.', attempt=1)
    assert list(tmp_path.iterdir()) == []
