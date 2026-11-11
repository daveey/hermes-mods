import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

HERE = Path(__file__).parent

def load(name):
    spec = importlib.util.spec_from_file_location('rotation_' + name, HERE / (name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_both_writers_use_bounded_journal(tmp_path, monkeypatch):
    watch, trace = load('watch'), load('reqtrace')
    calls = []
    for mod in (watch, trace):
        monkeypatch.setattr(mod.journal, 'append', lambda *a, **kw: calls.append(kw))
    path = tmp_path / 'events.jsonl'
    watch._append(path, {'kind': 'cpu_profile'})
    trace.default_sink(path)({'kind': 'request_stats'})
    trace.default_sink(path)({'kind': 'slow_request', 'awaiting': [], 'method': 'GET',
                            'path': '/test', 'total_s': 3, 'started': 'now', 'responded': True, 'samples': 1})
    assert calls == [{}, {'durable': True}, {}]


def test_rotation_bound_and_oversize(tmp_path):
    journal = load('journal')
    path = tmp_path / 'events.jsonl'
    for i in range(100):
        journal.append(path, {'i': i}, max_bytes=40, backups=3)
    files = [path, *sorted(tmp_path.glob('events.jsonl.[0-9]'))]
    assert len(files) == 4
    assert all(p.stat().st_size <= 40 for p in files)
    assert json.loads(path.read_text().splitlines()[-1]) == {'i': 99}
    before = path.read_bytes()
    with pytest.raises(OSError):
        journal.append(path, {'long': 'x' * 100}, max_bytes=40)
    assert path.read_bytes() == before


def test_concurrent_processes_preserve_records(tmp_path):
    path = tmp_path / 'events.jsonl'
    code = "import sys; sys.path.insert(0,sys.argv[1]); import journal; [journal.append(sys.argv[2], {'writer': sys.argv[3], 'i': i}, max_bytes=500, backups=50) for i in range(40)]"
    children = [subprocess.Popen([sys.executable, '-c', code, str(HERE), str(path), str(n)]) for n in range(4)]
    assert all(child.wait(timeout=20) == 0 for child in children)
    records = [json.loads(line) for p in tmp_path.glob('events.jsonl*') if p.suffix != '.lock'
               for line in p.read_text().splitlines()]
    assert len(records) == 160
    assert len({(r['writer'], r['i']) for r in records}) == 160


def test_stats_write_failure_propagates(tmp_path, monkeypatch):
    trace = load('reqtrace')
    def fail(*args, **kwargs):
        raise OSError('disk full')
    monkeypatch.setattr(trace.journal, 'append', fail)
    with pytest.raises(OSError):
        trace.default_sink(tmp_path / 'events')({'kind': 'request_stats'})
