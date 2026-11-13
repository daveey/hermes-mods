"""Owner-label coverage must be explicit, independently of the scan deadline."""
import importlib.util
import json
from pathlib import Path
import sys
import types

import pytest

spec = importlib.util.spec_from_file_location('roots_labels_test', Path(__file__).with_name('roots.py'))
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


def test_exact_capacity_is_not_overflow_and_existing_labels_stay_named(monkeypatch):
    monkeypatch.setattr(r, 'MAX_LABELS', 2)
    w = r._Walk(lambda: 0, 60)
    assert w.label('first') == 0
    assert w.label('second') == 1
    assert w.labels_truncated is False
    assert w.label('first') == 0
    assert w.labels_truncated is False
    assert w.label('third') == 2
    assert w.labels_truncated is True
    assert w.label('fourth') == 2
    assert w.label('second') == 1
    assert len(w.labels) == 3


@pytest.mark.parametrize('cap, overflow', [(100000, False), (20000, True)])
def test_late_large_owner_is_named_or_explicitly_overflowed(monkeypatch, tmp_path, cap, overflow):
    monkeypatch.setattr(r, 'MAX_LABELS', cap)
    module = types.ModuleType('fixture')
    for i in range(20001):
        setattr(module, f'root{i}', [])
    module.large = bytearray(1000000)
    # Isolate the fixture from the test runner's modules and suspended frames.
    monkeypatch.setattr(r.gc, 'get_objects', lambda: [])
    monkeypatch.setattr(r.sys, '_current_frames', lambda: {})
    scan = r.scan
    monkeypatch.setattr(r, 'scan', lambda: scan(modules={'fixture': module}))
    report = r.safe_scan()
    assert report['status'] == 'ok'
    assert report['truncated'] is False
    assert report['labels_truncated'] is overflow
    rows = {name: size for name, size, _ in report['top']}
    label = '<other owners>' if overflow else 'fixture.large'
    assert rows[label] >= sys.getsizeof(module.large)
    assert report['owners'] <= cap + 1
    # Exercise the production report writer and read the serialized contract.
    Probe = r._probe_class()
    probe = Probe(tmp_path, command=r.COMMAND, scanner=lambda: report, max_output=r.MAX_OUTPUT)
    try:
        probe.write('result.json', report)
        payload = (probe.path / 'result.json').read_bytes()
    finally:
        probe.close()
    assert len(payload) <= 65536
    assert json.loads(payload)['labels_truncated'] is overflow


def test_production_budget_covers_more_than_the_old_boundary():
    assert r.MAX_LABELS == 100000
