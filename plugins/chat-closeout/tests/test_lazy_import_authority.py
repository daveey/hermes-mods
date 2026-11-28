"""Lazy loaders retain complete modules and refuse missing import authority."""
import importlib.util
from pathlib import Path
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def plugin():
    spec = importlib.util.spec_from_file_location('_closeout_lazy_guard_controls', ROOT / '__init__.py')
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    module.__package__ = ''
    spec.loader.exec_module(module)
    return module


def test_cached_proxy_never_reloads_its_complete_module(plugin, monkeypatch):
    proxy = plugin._LazyModule('guard_control')
    complete = types.ModuleType('complete')
    object.__setattr__(proxy, '_module', complete)
    monkeypatch.setattr(plugin.importlib.util, 'spec_from_file_location', lambda *args: pytest.fail('cached proxy reloaded'))
    assert proxy._load() is complete
    assert proxy._load() is complete


def test_existing_file_module_never_reexecutes(plugin, monkeypatch):
    complete = types.ModuleType('chat_closeout_guard_control')
    monkeypatch.setitem(sys.modules, complete.__name__, complete)
    monkeypatch.setattr(plugin.importlib.util, 'spec_from_file_location', lambda *args: pytest.fail('existing file module reexecuted'))
    assert plugin._LazyModule('guard_control')._load() is complete


@pytest.mark.parametrize('authority', [None, types.SimpleNamespace(loader=None)])
def test_missing_import_authority_fails_before_module_creation(plugin, monkeypatch, authority):
    monkeypatch.delitem(sys.modules, 'chat_closeout_guard_control', raising=False)
    monkeypatch.setattr(plugin.importlib.util, 'spec_from_file_location', lambda *args: authority)
    monkeypatch.setattr(plugin.importlib.util, 'module_from_spec', lambda *args: pytest.fail('constructed without import authority'))
    with pytest.raises(AssertionError):
        plugin._LazyModule('guard_control')._load()
