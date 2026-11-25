"""Exclusive timing, read neutrality and actual lock contention."""
from contextlib import closing
import json
import sqlite3
import threading
import time

import pytest
from test_withdrawal import ROOT, load_module
from test_panel_membership import sources, membership

timing = load_module('panel_timing_test', ROOT / 'panel_timing.py')
mirror = load_module('panel_timing_mirror_test', ROOT / 'kanban_mirror.py')


def checked(trace):
    out = trace.finish()
    assert len(out['stages']) <= 66
    for key in ('wall_ns', 'cpu_ns'):
        assert sum(r[key] for r in out['stages'].values()) == out[key]
        assert all(r[key] >= 0 for r in out['stages'].values())
    return out


def test_exact_nested_exclusive_accounting_on_exception(monkeypatch):
    clocks = [0, 0]
    monkeypatch.setattr(timing.time, 'monotonic_ns', lambda: clocks[0])
    monkeypatch.setattr(timing.time, 'thread_time_ns', lambda: clocks[1])
    trace = timing.Recorder()
    with pytest.raises(ValueError), trace.stage('parent'):
        clocks[:] = [10, 5]
        with trace.stage('child'):
            clocks[:] = [30, 15]
        clocks[:] = [50, 25]
        raise ValueError('not recorded')
    clocks[:] = [60, 30]
    out = checked(trace)
    assert out['stages'] == {
        'parent': dict(wall_ns=30, cpu_ns=15, calls=1),
        'child': dict(wall_ns=20, cpu_ns=10, calls=1),
        'residual': dict(wall_ns=10, cpu_ns=5, calls=1)}
    assert not trace.stack


def test_bounded_aggregates_no_payload():
    trace = timing.Recorder()
    for _ in range(10000):
        with trace.stage('sqlite'):
            pass
    out = checked(trace)
    assert out['stages']['sqlite']['calls'] == 10000
    assert len(out['stages']) == 2
    assert trace.call_id != timing.Recorder().call_id
    assert 'not recorded' not in json.dumps(out)


def test_capture_read_semantics_and_unknown_fences_unchanged(sources):
    sources.put(sources.task(title='PRIVATE_PAYLOAD'))
    membership._INVENTORY.clear()
    trace = timing.Recorder()
    expected = sources.capture()
    actual = sources.capture(timing=trace)
    assert expected and actual == expected
    out = checked(trace)
    assert {'replica_sqlite', 'state_sqlite', 'ledger_sqlite',
            'inventory_hash_sample', 'inventory_lock_sample', 'membership_digest'} <= out['stages'].keys()
    assert 'PRIVATE_PAYLOAD' not in json.dumps(out)
    sources.sql('replica', "UPDATE meta SET v='1' WHERE k='offline_since'")
    assert sources.capture() is None
    assert sources.capture(timing=timing.Recorder()) is None


def test_sqlite_adapter_preserves_streaming_errors_and_close(tmp_path):
    path = tmp_path / 'fixture.db'
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('CREATE TABLE x(v)')
        conn.executemany('INSERT INTO x VALUES (?)', [(1,), (2,)])
        conn.commit()
    trace = timing.Recorder()
    with closing(trace.connect(mirror._open_ro, path, 'sqlite')) as conn:
        assert [tuple(r) for r in conn.execute('SELECT v FROM x ORDER BY v')] == [(1,), (2,)]
        with pytest.raises(sqlite3.Error):
            conn.execute('SELECT * FROM missing')
        assert not conn.in_transaction
    checked(trace)


def test_kept_reader_lock_attribution_and_thread_local_trace(tmp_path):
    path = tmp_path / 'fixture.db'
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('CREATE TABLE x(v)')
    entered, release, waiting = (threading.Event() for _ in range(3))
    result = []
    def holder():
        with mirror._kept(path):
            entered.set()
            assert release.wait(5)
    def reader():
        trace = timing.Recorder()
        with mirror.panel_trace(trace):
            waiting.set()
            with mirror._kept(path) as conn:
                conn.execute('SELECT * FROM x').fetchall()
        assert mirror._PANEL_TRACE.get() is None
        result.append(checked(trace))
    first, second = threading.Thread(target=holder), threading.Thread(target=reader)
    first.start()
    assert entered.wait(5)
    second.start()
    try:
        assert waiting.wait(5)
        time.sleep(.05)  # controlled disposable contention, never production
        assert mirror._PANEL_TRACE.get() is None
    finally:
        release.set()
        first.join(5)
        second.join(5)
        mirror.close_pool()
    assert not first.is_alive() and not second.is_alive()
    assert len(result) == 1
    lock = result[0]['stages']['reader_entry_lock']
    assert lock['wall_ns'] >= 30_000_000
    assert lock['cpu_ns'] < lock['wall_ns'] / 2
    assert 'local_sqlite' in result[0]['stages']


def test_api_carries_totals_and_exception_without_changing_result(tmp_path, monkeypatch):
    api = load_module('panel_timing_api_test', ROOT / 'dashboard' / 'plugin_api.py')
    monkeypatch.setattr(api, '_load', lambda name: timing if name == 'panel_timing' else mirror)
    def produce(*args, **kwargs):
        with mirror._stage('local_reconcile'):
            result = api.PanelReconcileResult()
            result.unchanged = True
            return result
    monkeypatch.setattr(api, '_panel_reconcile', produce)
    result = api.panel_reconcile(tmp_path, 'current', [])
    assert result == [] and result.unchanged
    assert result.panel_timing['stages']['local_reconcile']['calls'] == 1
    assert mirror._PANEL_TRACE.get() is None
    error = RuntimeError('source unavailable')
    def fail(*args, **kwargs):
        with mirror._stage('source_failure'):
            raise error
    monkeypatch.setattr(api, '_panel_reconcile', fail)
    with pytest.raises(RuntimeError) as caught:
        api.panel_reconcile(tmp_path, 'current', [])
    assert caught.value is error
    assert error.panel_timing['stages']['source_failure']['calls'] == 1
    assert mirror._PANEL_TRACE.get() is None
    assert 'source unavailable' not in json.dumps(error.panel_timing)
