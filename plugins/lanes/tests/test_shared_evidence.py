"""PR context and card projection share one request's history reads."""
import contextlib
import json

import pytest
import test_pr_projection as fixtures

board = fixtures.board


def test_strip_tokens_share_canonical_snapshot_accounting(board):
    import time
    api, connect, _calls = board
    stamp = int(time.time())
    with connect() as conn:
        for tokens in (100, 200):
            conn.execute("INSERT INTO task_events(task_id,kind,created_at,payload) VALUES('t_pr','worker_result',?,?)",
                         (stamp, json.dumps({'usage': {'input_tokens': tokens, 'output_tokens': 5}})))
        conn.commit()
    result = api.lane_view(6, '6h', False)
    assert result['configured'], result
    lane = result['lanes']['work']
    assert len(lane['strip']['tokens']) == 36
    assert sum(lane['strip']['tokens']) == lane['tokens'] == 205



@pytest.mark.parametrize('separate_evidence', [False, True])
def test_context_and_snapshot_read_history_once(board, monkeypatch, separate_evidence):
    flow = pytest.importorskip('hermes_cli.kanban_lanes_flow')
    if not hasattr(flow, 'Evidence'):
        pytest.skip('requires additive request-scoped Evidence core seam')
    from hermes_cli import kanban_db_connect as kbc
    api, connect, _calls = board
    queries = []

    @contextlib.contextmanager
    def traced():
        with connect() as conn:
            conn.set_trace_callback(queries.append)
            yield conn
    monkeypatch.setattr(kbc, 'connect_closing', traced)
    if separate_evidence:
        # Negative control: losing request sharing must produce a second read.
        contexts = api._pr_contexts

        def unshared(db, flow_module, *, evidence=None):
            return contexts(db, flow_module, evidence=flow_module.Evidence(db))

        monkeypatch.setattr(api, '_pr_contexts', unshared)
    result = api.flow('6h')
    assert result['configured'], result
    assert any(card['id'] == 't_pr' for card in result['cards']), result
    # Core may fetch one card with '=' or batch open cards with 'IN'. Count
    # ordered history reads in either shape, not only the old per-card spelling.
    histories = [q for q in queries
                 if 'from task_events where task_id' in ' '.join(q.lower().split())
                 and 'order by' in q.lower() and 'kind' in q.lower() and 'payload' in q.lower()]
    assert len(histories) == (2 if separate_evidence else 1), histories


@pytest.mark.parametrize('kind,payload', [
    ('heartbeat', '{}'), ('heartbeat', '{"head_sha":"next"}'),
    ('external_watch', '{}'), ('commented', 'null'),
    ('commented', '{"metadata":[]}'),
    ('scheduled', json.dumps({'awaiting': {'kind': 'ci', 'ref': fixtures.URL}})),
])
def test_shared_context_keys_equal_legacy_keys(board, kind, payload):
    flow = pytest.importorskip('hermes_cli.kanban_lanes_flow')
    if not hasattr(flow, 'Evidence'):
        pytest.skip('requires additive request-scoped Evidence core seam')
    api, connect, _calls = board
    with connect() as conn:
        conn.execute("INSERT INTO tasks(id,title,status,created_at,tenant) VALUES('second','second','scheduled',100,'work')")
        conn.execute("INSERT INTO task_events(task_id,kind,payload,created_at) VALUES('second','scheduled',?,100)",
                     (json.dumps({'awaiting': {'kind': 'ci', 'ref': fixtures.URL}}),))
        conn.execute("INSERT INTO task_events(task_id,kind,payload,created_at) VALUES('t_pr',?,?,100)", (kind, payload))
        if kind == 'scheduled':
            conn.execute("UPDATE tasks SET status='scheduled' WHERE id='t_pr'")
        assert api._pr_contexts(conn, flow, evidence=flow.Evidence(conn)) == api._pr_contexts(conn, flow)
