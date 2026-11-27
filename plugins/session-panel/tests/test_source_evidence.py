"""Strict evidence controls on real disposable SQL/coordinator/writer output.

No recorded production snapshots or fabricated successful receipt chains.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest
from test_core_readers import api, board
from test_reader_batch import graph
from test_source_coordinator_core import compose, drain
from test_feed import feedmod
from test_source_receipts import receipts, identity, read_log

spec = importlib.util.spec_from_file_location('source_evidence_under_test',
    Path(__file__).resolve().parents[1] / 'dashboard' / 'source_evidence.py')
se = importlib.util.module_from_spec(spec)
spec.loader.exec_module(se)


def source_before_projection(lane, log, monkeypatch):
    """Measure source-backed projections, not the intentionally source-less cold UI.

    Keep logging before watch/capture and audit every record. A fixture-only gate
    orders the first projection after a real accepted capture; no rows are filtered
    and no production/auditor policy is relaxed.
    """
    import threading
    acquired = threading.Event()
    record, ready = log.record, lane._projection_ready
    def capture_record(event, **data):
        record(event, **data)
        if event == 'capture_end' and data.get('disposition') == 'accepted':
            acquired.set()
    monkeypatch.setattr(log, 'record', capture_record)
    monkeypatch.setattr(lane, '_projection_ready', lambda session: acquired.is_set() and ready(session))
    return acquired


def test_measurement_gate_requires_accepted_source_receipt(monkeypatch):
    from types import SimpleNamespace
    events = []
    log = SimpleNamespace(record=lambda event, **data: events.append((event, data)))
    lane = SimpleNamespace(_projection_ready=lambda session: session == 'S')
    acquired = source_before_projection(lane, log, monkeypatch)
    assert not lane._projection_ready('S')
    log.record('capture_end', disposition='superseded')
    assert not acquired.is_set() and not lane._projection_ready('S')
    log.record('capture_end', disposition='accepted')
    assert acquired.is_set() and lane._projection_ready('S')
    assert not lane._projection_ready('other')
    assert [data['disposition'] for _, data in events] == ['superseded', 'accepted']


@pytest.fixture
def measured(graph, monkeypatch, tmp_path):
    r, local, remote, ksa = graph
    from hermes_cli import plugin_events
    monkeypatch.setattr(plugin_events, 'broadcast_plugin_event', lambda *args: None)
    owner_api = sys.modules[type(r).__module__]
    controls = {}
    # Independent canonical per-chat SQL path, before source composition; no
    # expectation is copied from an emitted/stored candidate model.
    control = feedmod.Feed(r, emit=lambda _: None, clock=lambda: 1800000000.)
    try:
        for session in ('S0', 'S1'):
            bundle = control.compute(session)
            controls[session] = [(section, row) for section, rows in bundle['model']['sections'].items()
                                 for row in rows if row['id'] == local]
    finally:
        control.stopped.set()
    f = feedmod.Feed(r, emit=owner_api._emit, clock=lambda: 1800000000.)
    log = receipts().ReceiptLog(tmp_path / 'measured.jsonl', identity={**identity(), 'serving_gateway': 'local-test'})
    f.install_receipts(log)  # BEFORE the first watch/acquisition/admission
    f.watch(['S0', 'S1'])
    lane = f.compose_board_sources(lambda: r, gateway='local-test', profile='default')
    source_before_projection(lane, log, monkeypatch)
    try:
        f.changes(['S0', 'S1'])
        drain(lane)
        lane._admit(['S0', 'S1'], 0)
        drain(lane)
        from fastapi import FastAPI
        from starlette.testclient import TestClient
        from hermes_cli.plugin_host_http_auth import with_hosted_admission
        monkeypatch.setattr(sys.modules[owner_api._FEED_KEY], 'PROCESS_FEED', f)
        monkeypatch.setattr(owner_api, '_request_profile', lambda: owner_api.LAUNCH_PROFILE)
        app = FastAPI()
        app.state.auth_required = True
        app.include_router(owner_api.router)
        with TestClient(with_hosted_admission(app, True)) as client:
            response = client.post('/changes', json={'sessions': ['S0', 'S1']})
        assert response.status_code == 200 and set(response.json()['bundles']) == {'S0', 'S1'}
    finally:
        f.stopped.set()
        lane.close()
        assert log.close()
    class Measurement(list):
        pass
    rows = Measurement(read_log(log.path))
    rows.controls = controls
    if destination := os.environ.get('B3_EVIDENCE'):
        (Path(destination) / 'cold-measured.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
    return rows


def test_real_cold_measurement_has_complete_evidence(measured):
    result = se.audit(measured)
    assert result['valid'], result


@pytest.mark.parametrize('event', ['source_received', 'projection_start', 'projection_end',
    'writer_request', 'writer_acquire', 'writer_release'])
def test_omitted_actual_stage_is_not_valid(measured, event):
    assert not se.audit([r for r in measured if r['event'] != event])['valid']


@pytest.mark.parametrize('field', ['covered_scopes', 'missing_scopes', 'dependencies'])
def test_missing_projection_field_rejects(measured, field):
    rows = copy.deepcopy(measured)
    for r in rows:
        if r['event'] == 'projection_end':
            r.pop(field, None)
    assert not se.audit(rows)['valid']


@pytest.mark.parametrize('field,value', [('feed_epoch', 'wrong'), ('producer_kind', 'wrong'),
    ('thread_birth', 'wrong'), ('acquired_ns', True)])
def test_wrong_writer_identity_or_clock_rejects(measured, field, value):
    rows = copy.deepcopy(measured)
    next(r for r in rows if r['event'] == 'writer_acquire')[field] = value
    assert not se.audit(rows)['valid']


@pytest.mark.parametrize('bad', [None, [None], ['{bad'], ['{\"a\":1,\"a\":2}'],
    [dict(schema='panel-source-receipts/1')]])
def test_malformed_fails_closed(bad):
    assert not se.audit(bad)['valid']


@pytest.mark.parametrize('kind', ['single_attempt', 'empty_dependencies', 'wrong_selector', 'admissions', 'late_receipt'])
def test_partial_stage_omissions_never_become_projection_carriage(measured, kind):
    rows = copy.deepcopy(measured)
    if kind == 'single_attempt':
        # Remove ONE repeat attempt with otherwise identical admission metadata.
        attempt = next(r['projection_attempt_id'] for r in reversed(rows) if r['event'] == 'projection_end')
        rows = [r for r in rows if r.get('projection_attempt_id') != attempt]
    elif kind == 'empty_dependencies':
        for r in rows:
            if r['event'] == 'projection_end':
                r['dependencies'] = {}
            if r['event'] == 'model_stored':
                r['publication']['dependencies'] = {}
            for p in r.get('publications', []):
                p['dependencies'] = {}
    elif kind == 'wrong_selector':
        for r in rows:
            receipt = r.get('receipt')
            if isinstance(receipt, dict):
                for _, demand in receipt['coverage']['captured_demands']['entries']:
                    demand['refs'] = ['contradictory selector']
    elif kind == 'admissions':
        rows = [r for r in rows if r['event'] != 'admitted']
    elif kind == 'late_receipt':
        later = max(r['monotonic_ns'] for r in rows) + 100
        for r in rows:
            if r['event'] == 'source_received':
                r['monotonic_ns'] = later
    assert not se.audit(rows)['valid']
