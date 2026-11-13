import importlib.util
import json
from pathlib import Path
import sys
import time
import tracemalloc

import pytest

_dir = Path(__file__).parent
spec = importlib.util.spec_from_file_location('heap_test', _dir / 'heap.py')
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
cspec = importlib.util.spec_from_file_location('heap_census_test', _dir / 'census.py')
c = importlib.util.module_from_spec(cspec)
cspec.loader.exec_module(c)


@pytest.fixture
def clean(monkeypatch):
    monkeypatch.delattr(sys, '_hermes_loop_watch_heap_census', raising=False)
    was = tracemalloc.is_tracing()
    yield
    monkeypatch.delattr(sys, '_hermes_loop_watch_heap_census', raising=False)
    if tracemalloc.is_tracing() and not was:
        tracemalloc.stop()


def _hold():
    return [bytes(4096) + str(i).encode() for i in range(512)]  # ~2 MB from one line


@pytest.mark.parametrize('value,expected', [(None, 0), ('', 0), ('x', 0), ('0', 0), ('33', 0), ('1', 1), ('8', 8)])
def test_frames_env(value, expected):
    assert h.frames({} if value is None else {h.ENV: value}) == expected


def test_default_off_leaves_tracing_and_disk_alone(tmp_path, clean):
    assert h.start(tmp_path, env={}) is None
    assert not tracemalloc.is_tracing()
    assert list(tmp_path.iterdir()) == []


def test_unknown_when_not_tracing(clean):
    report = h.scan()
    assert report['status'] == 'unknown' and report['by_line'] == []


def test_scan_attributes_a_held_allocation_to_its_line(clean):
    tracemalloc.start(8)
    held = _hold()
    report = h.scan()
    assert report['status'] == 'ok' and report['traces'] > 0
    line = _hold.__code__.co_firstlineno + 1
    key = f"{h._short(str(_dir / 'tests_heap.py'))}:{line}"
    top_app = {k: (size, n) for k, size, n in report['by_app_line']}
    assert key in top_app and top_app[key][0] >= 512 * 4096 and top_app[key][1] >= 512
    assert report['traced_bytes'] >= 512 * 4096
    del held


def test_report_has_no_values(clean):
    tracemalloc.start(4)
    secret = ['TOP-SECRET-%d' % i for i in range(1000)]
    text = json.dumps(h.scan())
    assert 'TOP-SECRET' not in text
    del secret


def test_one_shot_request_writes_capped_result(tmp_path, clean):
    thread = h.start(tmp_path, env={h.ENV: '4'})
    assert thread is not None and tracemalloc.is_tracing()
    assert h.start(tmp_path, env={h.ENV: '4'}) is None  # process-wide sentinel
    (d,) = (tmp_path / 'loop-watch-heap').iterdir()
    assert json.loads((d / 'ready.json').read_text())['command'] == 'heap-census-v1'
    time.sleep(0.01)
    req = d / 'request'
    req.write_bytes(h.COMMAND)
    req.chmod(0o600)
    thread.join(15)
    assert not thread.is_alive()
    out = d / 'result.json'
    assert (out.stat().st_mode & 0o777) == 0o600
    data = out.read_bytes()
    assert len(data) <= h.MAX_OUTPUT
    report = json.loads(data)
    assert report['kind'] == 'heap_census' and report['status'] == 'ok'
    assert report['by_line'] and report['by_app_entry']
    assert not tracemalloc.is_tracing()  # one-shot: tracing stops after the report


def test_failing_scan_still_writes_unknown(monkeypatch, clean):
    def boom():
        raise MemoryError
    monkeypatch.setattr(h, 'scan', boom)
    assert h.safe_scan() == {'schema': 1, 'kind': 'heap_census', 'status': 'unknown', 'error': 'MemoryError'}


def test_probe_failure_stops_tracing(tmp_path, clean):
    def broken(*a, **k):
        raise OSError('no dir')
    assert h.start(tmp_path, env={h.ENV: '2'}, probe_factory=broken) is None
    assert not tracemalloc.is_tracing()


def test_cache_command_does_not_trigger_heap_probe(tmp_path, clean):
    probe = c.Probe(tmp_path, command=h.COMMAND, scanner=h.scan, max_output=h.MAX_OUTPUT, subdir='loop-watch-heap')
    try:
        time.sleep(0.01)
        req = probe.path / 'request'
        req.write_bytes(c.COMMAND)
        req.chmod(0o600)
        probe.tick()
        assert probe.used and not (probe.path / 'result.json').exists()
    finally:
        probe.close()


def test_cache_probe_defaults_unchanged(tmp_path):
    probe = c.Probe(tmp_path, runner=lambda: None)
    try:
        assert probe.path.parent.name == 'loop-watch-census'
        assert json.loads((probe.path / 'ready.json').read_text())['command'] == 'cache-census-v1'
    finally:
        probe.close()
