"""Demotion must not re-decode an unchanged historical log on each state poll."""
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('coach_demotion_core', HERE / 'core.py')
assert spec and spec.loader
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


def row(ts, rule='one', **kw):
    return json.dumps(dict(ts=ts, event='choice', choice='dismissed', rule=rule, **kw)) + '\n'


def test_new_store_does_not_redecode_unchanged_log(tmp_path):
    log = tmp_path / 'log.jsonl'
    log.write_text(row(100) * 3 + json.dumps(dict(ts=100, event='other')) + '\n')
    original = json.loads
    with patch.object(json, 'loads', wraps=original) as loads:
        assert core.Store(tmp_path, clock=lambda: 101).demoted() == ['one']
        first = loads.call_count
        assert core.Store(tmp_path, clock=lambda: 102).demoted() == ['one']
        assert loads.call_count == first
        with log.open('a') as f:
            f.write(row(103, 'two'))
        core.Store(tmp_path, clock=lambda: 104).demoted()
        assert loads.call_count == first + 1


def test_rewrite_rotation_truncation_and_clock(tmp_path):
    log = tmp_path / 'log.jsonl'
    log.write_text(row(100) * 3)
    store = core.Store(tmp_path, clock=lambda: 101)
    assert store.demoted() == ['one']
    log.write_text(row(100, 'two') * 3)
    assert store.demoted() == ['two']
    log.rename(tmp_path / 'old')
    log.write_text(row(100, 'new') * 3)
    assert store.demoted() == ['new']
    log.write_text(row(100, 'new'))
    assert store.demoted() == []
    log.write_text(row(100, 'new') * 3)
    store.clock = lambda: 100 + core.DEMOTE_WINDOW_S + 1
    assert store.demoted() == []
    store.clock = lambda: 101
    assert store.demoted() == ['new']
    log.unlink()
    assert store.demoted() == []


def test_partial_tail_is_reparsed_when_completed(tmp_path):
    log = tmp_path / 'log.jsonl'
    log.write_text(row(100) * 2 + row(100).strip())
    store = core.Store(tmp_path, clock=lambda: 101)
    assert store.demoted() == ['one']
    with log.open('a') as f:
        f.write('garbage\n')
    assert store.demoted() == []
    with log.open('a') as f:
        f.write(row(100))
    assert store.demoted() == ['one']


def test_source_order_and_malformed_lines_match_legacy(tmp_path):
    log = tmp_path / 'log.jsonl'
    log.write_text(row(100) + 'bad\n' + row(-9999999) + row(100) + row(100))
    store = core.Store(tmp_path, clock=lambda: 101)
    assert store.demoted() == ['one']


def test_invalid_timestamp_keeps_legacy_error(tmp_path):
    log = tmp_path / 'log.jsonl'
    log.write_text(json.dumps(dict(ts='bad', event='other')) + '\n')
    with pytest.raises(TypeError):
        core.Store(tmp_path, clock=lambda: 101).demoted()


def test_rewrite_prefix_then_append_does_not_certify_old_rows(tmp_path):
    log = tmp_path / 'log.jsonl'
    log.write_text(row(100, 'one') * 3)
    store = core.Store(tmp_path, clock=lambda: 101)
    assert store.demoted() == ['one']
    log.write_text(row(100, 'two') * 3 + row(100, 'one'))
    assert store.demoted() == ['two']


def test_rewrite_with_restored_mtime_is_visible(tmp_path):
    import os
    log = tmp_path / 'log.jsonl'
    log.write_text(row(100, 'one') * 3)
    store = core.Store(tmp_path, clock=lambda: 101)
    assert store.demoted() == ['one']
    old = log.stat()
    log.write_text(row(100, 'two') * 3)
    os.utime(log, ns=(old.st_atime_ns, old.st_mtime_ns))
    assert store.demoted() == ['two']


def test_concurrent_fresh_stores_decode_each_completed_line_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    log = tmp_path / 'log.jsonl'
    log.write_text(row(100) * 3)
    # Load the sibling before replacing json.loads so module-loader work is excluded.
    core._cache.load_sibling(core._HERE, 'demotion_log')
    original = json.loads
    with patch.object(json, 'loads', wraps=original) as loads:
        with ThreadPoolExecutor(max_workers=4) as pool:
            result = list(pool.map(lambda _: core.Store(tmp_path, clock=lambda: 101).demoted(), range(8)))
        assert result == [['one']] * 8
        assert loads.call_count == 3


def test_concurrent_cold_modules_share_first_projection(tmp_path, monkeypatch):
    """Two independently bootstrapped core helpers must share cold initialization."""
    import sys
    import threading
    from concurrent.futures import ThreadPoolExecutor

    (tmp_path / 'log.jsonl').write_text(row(100) * 3)
    spec = importlib.util.spec_from_file_location('cold_coach_core', HERE / 'core.py')
    assert spec and spec.loader
    other = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(other)
    key = core._cache.module_key(HERE, 'demotion_log')
    monkeypatch.delitem(sys.modules, key, raising=False)
    entered = threading.Event()
    second_entered = threading.Event()
    release = threading.Event()
    second_called = threading.Event()
    original_spec = importlib.util.spec_from_file_location
    executions = []

    def gated_spec(name, path, *args, **kwargs):
        spec = original_spec(name, path, *args, **kwargs)
        if name == key:
            original_exec = spec.loader.exec_module

            def execute(module):
                executions.append(module)
                (entered if len(executions) == 1 else second_entered).set()
                assert release.wait(5)
                original_exec(module)
            spec.loader.exec_module = execute
        return spec

    def second_call():
        second_called.set()
        return other.Store(tmp_path, clock=lambda: 101).demoted()

    original_loads = json.loads
    with patch.object(importlib.util, 'spec_from_file_location', side_effect=gated_spec), \
            patch.object(json, 'loads', wraps=original_loads) as loads:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(core.Store(tmp_path, clock=lambda: 101).demoted)
            try:
                assert entered.wait(5)
                second = pool.submit(second_call)
                assert second_called.wait(5)
                second_entered.wait(0.25)
            finally:
                release.set()
            assert first.result(5) == second.result(5) == ['one']
        assert len(executions) == 1
        assert loads.call_count == 3


def test_path_cache_is_bounded_and_retains_no_unrelated_payload(tmp_path):
    module = core._cache.load_sibling(core._HERE, 'demotion_log')
    for i in range(12):
        root = tmp_path / str(i)
        root.mkdir()
        (root / 'log.jsonl').write_text(row(100) * 3 + json.dumps(dict(ts=100, event='other', payload='private')) + '\n')
        assert core.Store(root, clock=lambda: 101).demoted() == ['one']
    assert len(module._entries) <= 8
    assert 'private' not in repr(module._entries)


def test_arbitrary_precision_integer_timestamp_retains_legacy_behavior(tmp_path):
    (tmp_path / 'log.jsonl').write_text(row(10 ** 1000) * 3)
    assert core.Store(tmp_path, clock=lambda: 101).demoted() == ['one']
