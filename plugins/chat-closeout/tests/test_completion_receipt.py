"""Historical completion handoffs use canonical run projection, never summary inference."""
import json
import os
import sqlite3

import pytest
from test_closeout import core


@pytest.fixture
def receipt_core(monkeypatch):
    source = os.environ.get('HERMES_CORE_SOURCE')
    if not source:
        pytest.skip('core receipt projection integration requires HERMES_CORE_SOURCE')
    monkeypatch.syspath_prepend(source)
    import hermes_completion_receipt
    return hermes_completion_receipt


def receipt(disposition):
    item = {'scope': 'bounded assessment', 'disposition': disposition,
            'evidence': [{'ref': 'artifact:report', 'observed_at': '2026-01-01T00:00:00Z', 'verification': 'reported'}],
            'remaining': [{'scope': 'enforcement', 'state': 'deferred', 'owner': None, 'reason': 'No execution authorized.'}]}
    if disposition == 'superseded':
        item['superseded_by'] = 'decision:successor'
    return {'version': 1, 'items': [item]}


@pytest.mark.parametrize('disposition', ['delivered', 'rejected', 'superseded', 'contained'])
def test_receipt_closeout_preserves_evidence_and_deferred_scope(receipt_core, disposition):
    value = receipt(disposition)
    source = {'kind': 'task_run', 'ref': '@task:build@owner/t_one', 'event_uid': 'event-one', 'run_uid': 'run-one', 'completed_at': 1}
    prompt = core.closeout_prompt({'batch': ['t_one']}, {'t_one': {'status': 'done', 'completion_receipt': value, 'completion_receipt_source': source}})
    assert json.dumps(value, ensure_ascii=True, separators=(',', ':')) in prompt
    assert json.dumps(source, ensure_ascii=True, separators=(',', ':')) in prompt
    assert 'Deferred work is not authorized' in prompt
    assert 'containment is not a root-cause fix' in prompt
    assert 'historical completion receipt' in prompt


def test_legacy_closeout_never_infers_artifact_acceptance():
    prompt = core.closeout_prompt({'batch': ['t_one']}, {'t_one': {'status': 'done', 'summary': 'Looks delivered'}})
    assert 'artifact acceptance: unspecified' in prompt


def test_mixed_batch_keeps_legacy_followup_scoped_to_legacy(receipt_core):
    cards = {'old': {'status': 'done'}, 'new': {'status': 'done', 'completion_receipt': receipt('rejected')}}
    prompt = core.closeout_prompt({'batch': ['old', 'new']}, cards)
    assert 'For legacy cards without receipts (old)' in prompt
    assert 'Deferred work is not authorized' in prompt


def test_receipt_prompt_budget_names_omitted_source(receipt_core):
    data = receipt('delivered')
    data['items'] *= 32
    receipt_core.validate_completion_receipt(data)
    cards = {str(i): {'status': 'done', 'completion_receipt': data,
                     'completion_receipt_source': {'ref': f'@task:build@owner/t_{i}'}} for i in range(8)}
    prompt = core.closeout_prompt({'batch': list(cards)}, cards)
    assert 'details omitted (prompt budget)' in prompt
    assert '@task:build@owner/t_7' in prompt
    assert len(prompt) < 40000



def test_inventory_uses_completed_run_and_retains_owner_namespace(receipt_core, tmp_path):
    path = tmp_path / 'kanban.db'
    value = receipt('rejected')
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE tasks(id TEXT, status TEXT, title TEXT, created_on TEXT);
          CREATE TABLE task_events(id INTEGER, task_id TEXT, kind TEXT, run_id INTEGER, created_at INTEGER, uid TEXT, payload TEXT);
          CREATE TABLE task_runs(id INTEGER, task_id TEXT, outcome TEXT, summary TEXT, metadata TEXT, uid TEXT);''')
        db.execute("INSERT INTO tasks VALUES ('t_one','done','Assessment','source-gateway')")
        db.execute("INSERT INTO task_events VALUES (1,'t_one','completed',1,123,'portable-event',?)", (json.dumps({'completion_receipt_admission': 1}),))
        db.execute("INSERT INTO task_runs VALUES (1,'t_one','completed','Assessment rejected',?,'portable-run')", (json.dumps({'completion_receipt': value}),))
        db.execute("INSERT INTO task_runs VALUES (2,'t_one','review','Misleading newer prose',?,'review-run')", (json.dumps({'completion_receipt': receipt('delivered')}),))
    try:
        card = core.read_cards([path], ['t_one'])['t_one']
        assert card['completion_receipt'] == value
        source = card['completion_receipt_source']
        assert source['ref'] == '@task:default@source-gateway/t_one'
        assert source['event_uid'] == 'portable-event'
        assert source['run_uid'] == 'portable-run'
        assert source['completed_at'] == 123
        assert core.read_cards([path], ['t_one'])['t_one']['completion_receipt_source'] == source
        assert 'completion_receipt' not in core.read_cards([path], ['t_one'], light=True)['t_one']
        assert json.dumps(value, separators=(',', ':')) in core.closeout_prompt({'batch': ['t_one']}, {'t_one': card})
    finally:
        core._structural_reads.clear()
