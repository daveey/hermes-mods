"""Mocks must preserve lazily loaded module globals exactly like real modules."""
import importlib.util
from pathlib import Path
import sys
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('name, attribute', [('owner_wake', 'tick'), ('routing', '_fallback_cache')])
def test_mock_patch_restores_lazy_module_attribute(name, attribute):
    spec = importlib.util.spec_from_file_location('_closeout_mock_preservation', ROOT / '__init__.py')
    assert spec is not None and spec.loader is not None
    plugin = importlib.util.module_from_spec(spec)
    plugin.__package__ = ''
    spec.loader.exec_module(plugin)
    proxy = getattr(plugin, name)
    actual = proxy._load()
    original = getattr(actual, attribute)
    try:
        with mock.patch.object(proxy, attribute, object()):
            assert getattr(actual, attribute) is not original
        assert getattr(proxy, attribute) is original
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(proxy, attribute, object())
            assert getattr(actual, attribute) is not original
        assert getattr(proxy, attribute) is original
    finally:
        setattr(actual, attribute, original)
        sys.modules.pop('_closeout_mock_preservation', None)
