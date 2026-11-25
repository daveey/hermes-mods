"""Real coordinator -> bounded file sink -> offline stage analyzer integration."""
import json
import sys

from test_coordinator_observation import observed, graph, api, board
from test_source_receipts import receipts, identity, read_log
from test_source_analysis import A


def test_real_source_writer_emit_log_roundtrips_through_stage_analyzer(observed, monkeypatch, tmp_path):
    from hermes_cli import plugin_events
    from test_source_coordinator_core import drain
    r, f, lane, conn, remote, now = observed
    owner_api = sys.modules[type(r).__module__]
    emitted = []
    monkeypatch.setattr(plugin_events, 'broadcast_plugin_event', lambda *args: emitted.append(args))
    f.emit = owner_api._emit
    m, path = receipts(), tmp_path / 'actual-stage-receipts.jsonl'
    log = m.ReceiptLog(path, identity=identity())
    try:
        f.install_receipts(log)
        conn.execute("UPDATE tasks SET title='Measured semantic source write' WHERE id=?", (remote,))
        conn.commit()
        lane._capture_one(r, lane.external, ('stage-integration',))
        drain(lane)
    finally:
        # Measurement sink closes only after all source/projection producers.
        f.stopped.set()
        lane.close()
        assert log.close()
    rows = read_log(path)
    # This legacy warm fixture intentionally omits the initial admissions and
    # captures. It remains useful for timing diagnostics, never strict evidence.
    assert not A.analyze_records(rows, receipts=m, require_closed=True)['inputs_valid']
    report = A.analyze_records(rows, receipts=m, require_closed=True, strict_evidence=False)
    (tmp_path / 'actual-stage-analysis.json').write_text(json.dumps(report, indent=2, sort_keys=True))
    assert report['inputs_valid'], report
    assert report['acceptance'] == A.NOT_EVALUATED
    assert emitted and report['validator']['server_emitted'] > 0
    assert report['validator']['authenticated_catchup'] == 0
    events = {row['event'] for row in rows}
    assert {'capture_start', 'capture_end', 'source_received', 'admitted', 'projection_start',
            'projection_end', 'writer_request', 'writer_acquire', 'writer_release',
            'model_stored', 'emit_success', 'window_end'} <= events
    assert report['captures']['ended'] > 0
    projections = [row for row in rows if row['event'] == 'projection_end']
    assert projections
    for row in projections:
        assert row['projected_card_details'] == len(f.bundles[row['session']]['bundle']['cards'])
        bundle = f.bundles[row['session']]['bundle']
        assert row['projected_membership_rows'] == len(bundle['tasks']) + len(bundle['done'])
