"""Review regressions over disposable real readers and transports; no activation."""
import base64
import copy
import json
import sys

import pytest
from test_source_evidence import measured, graph, api, board, se
from test_source_oracle import expectations, analyze
from test_coordinator_observation import observed
from test_source_coordinator_core import drain
from test_source_receipts import receipts, identity, read_log
from test_source_analysis import A


def test_partial_receipt_admission_loss_is_invalid(measured):
    rows = copy.deepcopy(measured)
    admitted = [r for r in rows if r['event'] == 'admitted']
    attempt = admitted[-1]['capture_attempt_id']
    rows = [r for r in rows if not (r['event'] == 'admitted' and r.get('capture_attempt_id') == attempt)]
    assert any(r['event'] == 'admitted' for r in rows)
    assert not se.audit(rows)['valid']


@pytest.mark.parametrize('field', ['feed_epoch', 'coordinator_epoch', 'owner_token'])
def test_capture_identity_must_join_projection(measured, field):
    rows = copy.deepcopy(measured)
    attempt = next(r['capture_attempt_id'] for r in rows if r['event'] == 'source_received')
    for row in rows:
        if row.get('capture_attempt_id') == attempt and field in row:
            row[field] = 'contradictory-capture-identity'
    assert not se.audit(rows)['valid']


def test_empty_dependency_values_cannot_hide_displayed_cards(measured):
    rows = copy.deepcopy(measured)
    def empty(value):
        for _, keys in value.get('entries', []):
            keys.clear()
    for row in rows:
        if row['event'] == 'projection_end':
            empty(row['dependencies'])
        if row['event'] == 'model_stored':
            empty(row['publication']['dependencies'])
        for pub in row.get('publications', []):
            empty(pub['dependencies'])
    assert not se.audit(rows)['valid']


def test_absence_rejects_noncanonical_owner_key(measured):
    manifest = expectations(measured)
    for case in manifest['cases']:
        case['rows'][0]['section'] = None
        case['rows'][0]['owner']['key'] = 'not-the-owner-key'
    result = analyze(measured, manifest)
    assert result['semantic_oracle'] == 'FAIL' and not result['inputs_valid']


def test_superseded_emit_after_http_delivery_has_no_prior(observed, monkeypatch, tmp_path):
    from hermes_cli import plugin_events
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from hermes_cli.plugin_host_http_auth import with_hosted_admission
    r, f, lane, conn, remote, now = observed
    owner_api = sys.modules[type(r).__module__]
    succeeds = [True]
    def transport(*args):
        if not succeeds[0]:
            raise OSError('fixture refused emission')
    monkeypatch.setattr(plugin_events, 'broadcast_plugin_event', transport)
    f.emit = owner_api._emit
    log = receipts().ReceiptLog(tmp_path / 'delivery.jsonl', identity=identity())
    def title(text):
        conn.execute('UPDATE tasks SET title=? WHERE id=?', (text, remote)); conn.commit()
        lane._capture_one(r, lane.external, ('review-title', text))
        drain(lane)
    try:
        f.install_receipts(log)
        title('Review model A')
        succeeds[0] = False
        title('Review model B')
        monkeypatch.setattr(sys.modules[owner_api._FEED_KEY], 'PROCESS_FEED', f)
        monkeypatch.setattr(owner_api, '_request_profile', lambda: owner_api.LAUNCH_PROFILE)
        app = FastAPI(); app.state.auth_required = True; app.include_router(owner_api.router)
        with TestClient(with_hosted_admission(app, True)) as client:
            response = client.post('/changes', json={'sessions': ['S0', 'S1']})
        assert response.status_code == 200 and 'Review model B' in response.text
        title('Review model A')
        for _ in range(2):
            lane._admit(['S0', 'S1'], 0); drain(lane)
    finally:
        f.stopped.set(); lane.close(); assert log.close()
    rows = read_log(log.path)
    delivered = next(r['monotonic_ns'] for r in rows if r['event'] == 'catchup_success')
    unchanged = [r for r in rows if r['event'] == 'no_change' and r['monotonic_ns'] > delivered]
    assert unchanged and all(r.get('prior_successful_publication') is None for r in unchanged)
    # Offline auditing must also reject the original superseded A reference if a
    # malformed producer claims it; do not fabricate a successful publication.
    bad = copy.deepcopy(rows)
    for row in bad:
        if row['event'] == 'no_change' and row['monotonic_ns'] > delivered:
            candidates = [p for e in rows if e['event'] == 'emit_success' for p in e.get('publications', []) if p['session'] == row['chat']['session_id']]
            row['prior_successful_publication'] = candidates[-1]
    result = A.analyze_records(bad, receipts=receipts(), strict_evidence=False)
    assert result['no_change_evidence']['invalid'] > 0


@pytest.mark.parametrize('cold_before_acquisition', [False, True])
def test_real_reducer_offlist_blocker_defeats_absence(graph, board, monkeypatch, tmp_path, cold_before_acquisition):
    from hermes_cli import kanban_db
    from test_feed import feedmod
    if cold_before_acquisition:
        import threading
        coordinator = feedmod.Feed._source_module().SourceCoordinator
        projected = threading.Event()
        register, capture, project = coordinator._register, coordinator._capture_one, coordinator._project

        def register_first(self, *args, **kwargs):
            result = register(self, *args, **kwargs)
            self.service()
            assert projected.wait(5), 'cold projection never ran after registration'
            return result

        def capture_after_projection(self, *args, **kwargs):
            assert projected.wait(10), 'cold projection never ran'
            return capture(self, *args, **kwargs)

        def project_first(self, session):
            try:
                return project(self, session)
            finally:
                projected.set()

        monkeypatch.setattr(coordinator, '_register', register_first)
        monkeypatch.setattr(coordinator, '_capture_one', capture_after_projection)
        monkeypatch.setattr(coordinator, '_project', project_first)
    r, _, _, _ = graph
    _, conn = board
    oracle_module = A.load_sibling('source_oracle.py')
    # Match the canonical-reader warmup in the measured positive control.
    control = feedmod.Feed(r, emit=lambda _: None, clock=lambda: 1800000000.)
    try:
        control.compute('S0'); control.compute('S1')
    finally:
        control.stopped.set()
    feed = feedmod.Feed(r, emit=lambda _: None, clock=lambda: 1800000000.)
    log = receipts().ReceiptLog(tmp_path / 'offlist.jsonl', identity=identity())
    feed.install_receipts(log)
    feed.watch(['S0', 'S1'])
    lane = feed.compose_board_sources(lambda: r, gateway='local-test', profile='default')
    try:
        parent = kanban_db.create_task(conn, title='Off-list blocker', session_id='other', initial_status='running')
        for title in ('First child', 'Second child'):
            kanban_db.create_task(conn, title=title, session_id='S0', parents=[parent])
        feed.changes(['S0', 'S1'])
        drain(lane)
    finally:
        feed.stopped.set(); lane.close(); assert log.close()
    rows = read_log(log.path)
    event = next(row for row in reversed(rows) if row['event'] == 'model_stored' and row['publication']['session'] == 'S0')
    body = json.loads(base64.b64decode(event['payload_b64']))
    group = next(g for g in body['model']['groups'] if g['id'] == parent)
    assert group['key'] == f'blocker:local-test:default:default:{parent}'
    pub = event['publication']
    owner = dict(id=parent, key=f'local-test:default:default:{parent}', gateway='local-test', profile='default', board='default')
    manifest = dict(schema='panel-semantic-expectations/1', cases=[dict(window_id=event['window_id'],
        admission=pub['admission'], rows=[dict(owner=owner, section=None)])])
    oracle = oracle_module.Oracle(manifest)
    assert oracle.check(event['window_id'], pub, body) is False
    assert oracle.report()['verdict'] == 'FAIL'
    audit = se.audit(rows)
    assert audit['valid'], audit
    if cold_before_acquisition:
        cold = [row for row in rows if row['event'] == 'model_stored'
                and row['publication']['source_vector'] == {}]
        assert cold and all(row['publication']['missing_scopes'] for row in cold)
        assert any(row['event'] == 'projection_start' and row['pinned_versions'] == {} for row in rows)
        # Empty pins are valid unknown coverage, never fabricated absence.
        bad = copy.deepcopy(rows)
        next(row for row in bad if row['event'] == 'model_stored'
             and row['publication']['source_vector'] == {})['publication']['missing_scopes'] = {}
        assert not se.audit(bad)['valid']
        bad = copy.deepcopy(rows)
        next(row for row in bad if row['event'] == 'projection_start'
             and row['pinned_versions'] == {})['pinned_versions'] = None
        assert not se.audit(bad)['valid']

