"""Trusted deployment adapters use the actual gate registry, never a second import."""
import pytest
from test_approval_reconciliation import call, setup


class Context:
    def __init__(self, paths):
        self.paths = paths
        self.hooks = {}
        self.disposers = []

    def get_config(self, key, default=None):
        return self.paths if key == 'approval_adapters' else default

    def register_hook(self, name, fn):
        self.hooks[name] = fn

    def on_unload(self, fn):
        self.disposers.append(fn)

    def unload(self):
        for dispose in reversed(self.disposers):
            dispose()


def adapter(tmp_path, body=None):
    p = tmp_path / 'adapter.py'
    p.write_text(body or '''import time

def register(registry):
    target = dict(authority='example', subject_kind='merge_pr',
                  subject='example/project#123', revision='a'*40)
    registry.register('private-store', lambda context: dict(target),
        lambda current: dict(target=current, state='approved', verified=True, observed_at=time.time()))
''')
    return str(p)


def test_configured_adapter_reaches_real_structured_hook_and_unloads(gate, tmp_path):
    ctx = Context([adapter(tmp_path)])
    gate.register(ctx)
    try:
        result = ctx.hooks['pre_tool_call'](tool_name='kanban_block', session_id='s1', args={
            'kind': 'needs_input', 'human_request': {
                'action': 'Approve merge', 'why_human': 'User review required',
                'scope': 'example/project#123', 'evidence': 'inspect', 'interaction': 'approval'}})
        assert 'already approved' in result['message']
        assert 'private-store' in gate.approval_reconciliation._ADAPTERS
    finally:
        ctx.unload()
    assert 'private-store' not in gate.approval_reconciliation._ADAPTERS


@pytest.mark.parametrize('paths', ['not-a-list', [42], ['relative.py'], ['/missing/adapter.py']])
def test_config_failure_keeps_operational_hold_not_silent_unmanaged(gate, paths):
    ctx = Context(paths)
    gate.register(ctx)
    try:
        result = call(gate)
        assert 'operational recovery' in result['message']
    finally:
        ctx.unload()


@pytest.mark.parametrize('error', ['RuntimeError', 'SystemExit'])
def test_partial_registration_failure_rolls_back_and_holds(gate, tmp_path, error):
    path = adapter(tmp_path, '''def register(registry):
    registry.register('partial', lambda c: None, lambda t: None)
    raise ERROR('private-sensitive-location')
'''.replace('ERROR', error))
    ctx = Context([path])
    gate.register(ctx)
    try:
        assert 'partial' not in gate.approval_reconciliation._ADAPTERS
        result = call(gate)
        assert 'operational recovery' in result['message']
        assert 'private-sensitive-location' not in result['message']
    finally:
        ctx.unload()


def test_duplicate_adapter_rejects_without_removing_foreign_registration(gate, tmp_path):
    dispose = gate.approval_reconciliation.register('private-store', lambda c: None, lambda t: None)
    ctx = Context([adapter(tmp_path)])
    gate.register(ctx)
    try:
        assert 'operational recovery' in call(gate)['message']
    finally:
        ctx.unload()
    assert 'private-store' in gate.approval_reconciliation._ADAPTERS
    dispose()


def test_unconfigured_keeps_old_gate_and_reload_has_no_duplicate(gate, tmp_path):
    empty = Context([])
    gate.register(empty)
    assert not empty.disposers
    path = adapter(tmp_path)
    for _ in range(2):
        ctx = Context([path])
        gate.register(ctx)
        try:
            assert 'already approved' in call(gate)['message']
        finally:
            ctx.unload()
