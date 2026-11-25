"""Original B3 behavioral RED control, now the prior-success regression.

Disposable real CoreReaders/coordinator/writer/serializer/emit path; no activation.
The original assertions are preserved against B3a's private evidence change.
"""
import json
import os
from pathlib import Path
import sys

import pytest

from test_coordinator_observation import observed, graph, api, board
from test_source_coordinator_core import drain
from test_source_receipts import receipts, identity, read_log
from test_source_analysis import A


@pytest.mark.parametrize('emit_succeeds', [True, False])
def test_unchanged_model_names_only_actual_prior_success(observed, monkeypatch, tmp_path, emit_succeeds):
    from hermes_cli import plugin_events
    r, f, lane, conn, remote, now = observed
    owner_api = sys.modules[type(r).__module__]
    transport_calls = []

    def transport(*args):
        transport_calls.append(args)
        if not emit_succeeds:
            raise OSError('fixture transport refused model')

    monkeypatch.setattr(plugin_events, 'broadcast_plugin_event', transport)
    f.emit = owner_api._emit
    log = receipts().ReceiptLog(tmp_path / 'no-change.jsonl', identity=identity())
    try:
        f.install_receipts(log)
        conn.execute("UPDATE tasks SET title='B3 actual prior publication' WHERE id=?", (remote,))
        conn.commit()
        lane._capture_one(r, lane.external, ('b3-prior-success-control',))
        drain(lane)
        for _ in range(2):
            lane._admit(['S0', 'S1'], 0)
            drain(lane)
    finally:
        f.stopped.set()
        lane.close()
        assert log.close(), (log.dropped_records, log.write_errors, log.encoding_errors)
    rows = read_log(log.path)
    report = A.analyze_records(rows, receipts=receipts(), require_closed=True, strict_evidence=False)
    if destination := os.environ.get('B3_EVIDENCE'):
        out = Path(destination)
        out.mkdir(parents=True, exist_ok=True)
        stem = 'emit-success' if emit_succeeds else 'emit-refused'
        (out / (stem + '.jsonl')).write_text(''.join(json.dumps(row) + '\n' for row in rows))
        (out / (stem + '-analysis.json')).write_text(json.dumps(report, indent=2) + '\n')
    assert report['inputs_valid'], report
    assert transport_calls, 'preflight never reached actual serializer/emitter boundary'
    unchanged = [row for row in rows if row['event'] == 'no_change']
    assert unchanged, 'preflight did not exercise the unchanged outcome'
    successes = [publication for row in rows if row['event'] == 'emit_success'
                 for publication in row.get('publications', [])]
    if not emit_succeeds:
        assert not successes
        assert all(row.get('prior_successful_publication') is None for row in unchanged)
        return
    assert successes, 'positive control did not record an actual successful model emission'
    for row in unchanged:
        session = row['chat']['session_id']
        previous = [p for p in successes if p['session'] == session]
        assert previous, 'unchanged control has no successful same-chat predecessor'
        # DESIGN section8: no_change must carry the prior successfully emitted
        # revision; storage or generic callback return cannot substitute for it.
        assert row.get('prior_successful_publication') == previous[-1], (
            'no_change lost prior exact successful publication custody', session,
            row.get('prior_successful_publication'), previous[-1]['model_revision'])
