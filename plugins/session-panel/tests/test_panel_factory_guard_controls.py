"""Provider factory/bootstrap boundaries, independent of real-exec wire controls."""
import importlib.util
from pathlib import Path
import sys
import types

import pytest


@pytest.fixture
def provider():
    path = Path(__file__).resolve().parents[1] / 'dashboard' / 'producer_provider.py'
    spec = importlib.util.spec_from_file_location('_panel_factory_guard_controls', path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_provider_factory_bootstraps_exact_declared_registry_before_construction(provider, monkeypatch):
    calls = []
    manifest = {'callback_registry': {'done': [], 'waiting': []}}
    fake = types.ModuleType('_provider_api_control')
    fake._callback_bootstrap_mod = lambda: types.SimpleNamespace(bootstrap=lambda actual: calls.append(('bootstrap', actual)))
    loader = types.SimpleNamespace(exec_module=lambda module: calls.append(('exec', module)))
    monkeypatch.delitem(sys.modules, 'gateway.run', raising=False)
    monkeypatch.delitem(sys.modules, 'run_agent', raising=False)
    monkeypatch.setattr(provider.importlib.util, 'spec_from_file_location', lambda *args: types.SimpleNamespace(loader=loader))
    monkeypatch.setattr(provider.importlib.util, 'module_from_spec', lambda spec: fake)
    monkeypatch.setitem(sys.modules, '_session_panel_producer_api_v1', None)
    sentinel = object()
    def construct(api, **kwargs):
        calls.append(('construct', api, kwargs))
        return sentinel
    monkeypatch.setattr(provider, 'PanelProducer', construct)
    home, installation = object(), object()
    actual = provider.open_provider({'provider': {'manifest': manifest}, 'home_context': home, 'installation': installation})
    assert actual is sentinel
    assert calls == [('exec', fake), ('bootstrap', manifest), ('construct', fake, {'home_context': home, 'installation': installation})]


def test_provider_contiguous_events_never_prepare_reset_snapshot(provider):
    producer = object.__new__(provider.PanelProducer)
    producer.ring = provider.EventRing()
    class Trap:
        @property
        def lock(self):
            pytest.fail('snapshot prepared for contiguous event batch')
    producer.feed = Trap()
    producer.ring.append({'rev': 1})
    assert producer.events_since(0) == {'seq': 1, 'events': [[1, {'rev': 1}]], 'reset': False}
