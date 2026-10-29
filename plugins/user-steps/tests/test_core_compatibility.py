"""Exercise optional/required core boundaries without an installed Hermes checkout."""
import sys
import types

import pytest

from test_decisions import ROOT, load_module


def test_early_answer_fallback_loads_before_module_publication(monkeypatch):
    monkeypatch.setitem(sys.modules, 'tools.clarify_early', None)
    name = 'unpublished_early_answer_fallback'
    assert name not in sys.modules
    early = load_module(name, ROOT / 'clarify_early.py')
    calls = []
    result = {'status': 'answered_elsewhere'}
    answer = early.EarlyAnswer(result, lambda: calls.append('committed'))
    assert answer.result is result
    assert calls == []
    answer.commit()
    assert calls == ['committed']
    assert name not in sys.modules


def test_early_answer_prefers_core_type(monkeypatch):
    core = types.ModuleType('tools.clarify_early')
    sentinel = type('CoreEarlyAnswer', (), {})
    core.__dict__['EarlyAnswer'] = sentinel
    monkeypatch.setitem(sys.modules, 'tools.clarify_early', core)
    early = load_module('early_answer_with_core', ROOT / 'clarify_early.py')
    assert early.EarlyAnswer is sentinel


@pytest.mark.parametrize('contract', [
    None, {}, {'needs_buffer': True}, {'post_transform_release': True},
    {'needs_buffer': False, 'post_transform_release': True},
    {'needs_buffer': True, 'post_transform_release': False},
    {'needs_buffer': 1, 'post_transform_release': True},
])
def test_registration_rejects_incompatible_publication_contract(monkeypatch, contract):
    gate = types.ModuleType('agent.pre_final_gate')
    gate.__dict__['PRE_FINAL_GATE_CAPABILITIES'] = contract
    monkeypatch.setitem(sys.modules, 'agent.pre_final_gate', gate)
    plugin = load_module('unsupported_publication_plugin', ROOT / '__init__.py')

    class Registry:
        def register_hook(self, *args): pytest.fail('must reject before registering hooks')
        def register_tool(self, **kwargs): pytest.fail('must reject before registering tools')

    with pytest.raises(RuntimeError, match='update core first'):
        plugin.register(Registry())


def test_registration_rejects_missing_publication_module(monkeypatch):
    monkeypatch.setitem(sys.modules, 'agent.pre_final_gate', None)
    plugin = load_module('missing_publication_plugin', ROOT / '__init__.py')
    with pytest.raises(RuntimeError, match='update core first'):
        plugin.register(object())
