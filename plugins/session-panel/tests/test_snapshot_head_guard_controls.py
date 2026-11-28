"""Snapshot header and body JSON type checks must each refuse independently."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest


@pytest.fixture
def api(monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'dashboard' / 'plugin_api.py'
    spec = importlib.util.spec_from_file_location('_panel_snapshot_head_guards', path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('invalid', [[], ['not-an-object'], None, 1, 'text'])
def test_snapshot_header_type_refuses_with_valid_body(api, tmp_path, invalid):
    path = tmp_path / 'snapshot'
    snap = {'epoch': 'test-epoch'}
    path.write_text(json.dumps(invalid) + '\n' + json.dumps(snap), encoding='utf-8')
    assert api.load_snapshot(path, now=1000) is None


@pytest.mark.parametrize('invalid', [[], ['not-an-object'], None, 1, 'text'])
def test_snapshot_body_type_refuses_with_valid_header(api, tmp_path, invalid):
    path = tmp_path / 'snapshot'
    head = {'feed': api.FEED_ID, 'profile': api.LAUNCH_PROFILE,
            'epoch': 'test-epoch', 'saved_at': 1000}
    path.write_text(json.dumps(head) + '\n' + json.dumps(invalid), encoding='utf-8')
    assert api.load_snapshot(path, now=1000) is None


def test_snapshot_valid_header_body_positive(api, tmp_path):
    path = tmp_path / 'snapshot'
    head = {'feed': api.FEED_ID, 'profile': api.LAUNCH_PROFILE,
            'epoch': 'test-epoch', 'saved_at': 1000}
    snap = {'epoch': 'test-epoch', 'payload': {'kept': True}}
    path.write_text(json.dumps(head) + '\n' + json.dumps(snap), encoding='utf-8')
    assert api.load_snapshot(path, now=1000) == snap
