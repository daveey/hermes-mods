import subprocess
from types import SimpleNamespace

import pytest

from plugins.runners.scripts import runtime_reconcile as runtime


@pytest.mark.parametrize('failure', [None, 'reconcile', 'stop'])
def test_supervisor_always_restores_collector(failure):
    calls = []
    def command(argv, **kwargs):
        calls.append(argv[2])
        if argv[2] == failure:
            raise subprocess.TimeoutExpired(argv, 120)
        return SimpleNamespace(returncode=0)
    def run():
        calls.append('reconcile')
        if failure == 'reconcile':
            raise RuntimeError('failed')
        return 0
    if failure:
        with pytest.raises((RuntimeError, subprocess.TimeoutExpired)):
            runtime.supervised(run, 'hermes-runners-collector.service', command)
    else:
        assert runtime.supervised(run, 'hermes-runners-collector.service', command) == 0
    assert calls == (['is-active', 'stop', 'start'] if failure == 'stop' else ['is-active', 'stop', 'reconcile', 'start'])


def test_inactive_collector_is_not_started():
    calls = []
    def command(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=3)
    with pytest.raises(RuntimeError, match='not_active'):
        runtime.supervised(lambda: pytest.fail('entered'), 'hermes-runners-collector.service', command)
    assert len(calls) == 1


def test_runtime_entry_activates_before_supervision(monkeypatch):
    from plugins.runners.scripts import runtime_collector, reconcile_scopes
    calls = []
    monkeypatch.setattr(runtime_collector, 'main', lambda args: calls.append(('bootstrap', args)) or 0)
    monkeypatch.setattr(reconcile_scopes, 'main', lambda args: calls.append(('reconcile', args)) or 0)
    monkeypatch.setattr(runtime, 'supervised', lambda run, unit: calls.append(('supervise', unit)) or run())
    assert runtime.main(['--core-root', '/core', '--config', '/config']) == 0
    assert calls == [('bootstrap', ['--core-root', '/core', '--check-core']),
                     ('supervise', 'hermes-runners-collector.service'), ('reconcile', ['--config', '/config'])]
