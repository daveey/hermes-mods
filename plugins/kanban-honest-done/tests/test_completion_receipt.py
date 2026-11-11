"""Structured receipt admission across the guard's existing entry paths."""
import importlib.util
import json
import os
from pathlib import Path
import shlex

import pytest


@pytest.fixture
def plugin(monkeypatch):
    source = os.environ.get('HERMES_CORE_SOURCE')
    if not source:
        pytest.skip('requires core receipt validator')
    monkeypatch.syspath_prepend(source)
    spec = importlib.util.spec_from_file_location('honest_receipt_test', Path(__file__).parents[1] / '__init__.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def value():
    return {'version': 1, 'items': [{'scope': 'report', 'disposition': 'delivered',
        'evidence': [{'ref': 'artifact:report', 'observed_at': '2026-01-01T00:00:00Z', 'verification': 'reported'}],
        'remaining': [{'scope': 'enforcement', 'state': 'deferred', 'owner': None,
                       'reason': 'Enforcement not implemented; user deferred and no execution authorized.'}]}]}


def call(plugin, surface, metadata, summary='Report delivered.'):
    args = {'task_id': 't_12345678', 'summary': summary, 'metadata': metadata}
    command = 'hermes kanban complete t_12345678 --summary ' + shlex.quote(summary) + ' --metadata ' + shlex.quote(json.dumps(metadata))
    if surface == 'direct':
        name = 'kanban_complete'
    elif surface == 'wrapped':
        name, args = 'tool_call', {'calls': [{'name': 'kanban_complete', 'arguments': args}]}
    elif surface == 'terminal':
        name, args = 'terminal', {'command': command}
    else:
        name, args = 'execute_code', {'code': 'terminal(' + repr(command) + ')'}
    return plugin.classify(name, args, 't_12345678', paths_fn=lambda: [])


@pytest.mark.parametrize('surface', ['direct', 'wrapped', 'terminal', 'execute_code'])
def test_receipt_malformed_is_refused_on_each_guard_surface(plugin, surface):
    assert 'completion_receipt' in call(plugin, surface, {'completion_receipt': {'version': 1, 'items': []}})


@pytest.mark.parametrize('surface', ['direct', 'wrapped', 'terminal', 'execute_code'])
def test_receipt_deferred_scope_does_not_request_new_execution(plugin, surface):
    assert call(plugin, surface, {'completion_receipt': value()}) is None


@pytest.mark.parametrize('surface', ['direct', 'wrapped', 'terminal', 'execute_code'])
def test_receipt_never_waives_legacy_summary_admission(plugin, surface):
    assert call(plugin, surface, {'completion_receipt': value()}, 'Deployment not verified.')


@pytest.mark.parametrize('surface', ['direct', 'wrapped', 'terminal', 'execute_code'])
def test_receipt_scope_cannot_hide_dropped_work(plugin, surface):
    data = value()
    data['items'][0]['scope'] = 'Deployment not verified.'
    assert call(plugin, surface, {'completion_receipt': data})


@pytest.mark.parametrize('surface', ['direct', 'wrapped', 'terminal', 'execute_code'])
def test_old_loaded_writer_refused_even_with_new_helper_files(plugin, surface, monkeypatch):
    from hermes_cli import kanban_db
    monkeypatch.setattr(kanban_db, 'complete_task', lambda *a, **kw: None)
    assert 'loaded writer' in call(plugin, surface, {'completion_receipt': value()})

