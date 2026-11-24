"""Fail-closed captures must not pay for global inventory they cannot certify."""
import json

from test_panel_membership import sources, core, GW  # noqa: F401


def test_local_authority_unknown_before_replica_open(sources):
    s = sources
    s.obligation()
    raw = s.sql('ledger', 'SELECT binding_json FROM origin_obligations')[0][0]
    binding = json.loads(raw)
    binding['owner']['gateway'] = GW
    s.sql('ledger', 'UPDATE origin_obligations SET binding_json=?', (json.dumps(binding),))
    opened = []
    def connect(path, **kwargs):
        opened.append(path)
        return core._ro_connect(path, **kwargs)
    assert s.capture(connect=connect) is None
    assert s.paths['replica'] not in opened


def test_mismatched_owner_checked_before_inventory_decode(sources):
    s = sources
    s.obligation()
    s.sql('replica', "UPDATE replica SET version=2 WHERE coll='tasks'")
    queries = []
    def connect(path, **kwargs):
        conn = core._ro_connect(path, **kwargs)
        if path == s.paths['replica']:
            conn.set_trace_callback(queries.append)
        return conn
    assert s.capture(connect=connect) is None
    assert not any(q == "SELECT gw,key,data,version FROM replica WHERE coll='tasks'" for q in queries)
    # The unknown check is not a sticky cache: recovery needs no notification.
    s.sql('replica', "UPDATE replica SET version=1 WHERE coll='tasks'")
    assert s.capture(connect=connect) is not None
