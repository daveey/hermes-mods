"""The unchanged transcript probe must not rebuild every lineage key."""
import sqlite3

from test_waiting_inputs import world, plugin


def test_unchanged_lineages_reuse_projection(world, monkeypatch):
    sids = ['c1', 'c2']
    plugin._lineages(sids)  # cold read can create/remove SQLite sidecars
    first = plugin._lineages(sids)
    real = plugin.wi.lineage_key
    calls = []
    def counted(*args):
        calls.append(args)
        return real(*args)
    monkeypatch.setattr(plugin.wi, 'lineage_key', counted)
    assert plugin._lineages(sids) == first
    assert calls == []
    # A committed message must invalidate the local projection gate.
    with sqlite3.connect(world['state']) as conn:
        conn.execute("INSERT INTO messages(session_id, role, content, timestamp, active) VALUES ('c1','user','ack',101,1)")
    assert plugin._lineages(sids) != first
    assert calls
    calls.clear()
    assert set(plugin._lineages(['c2'])) == {'c2'}
    assert calls


def test_failed_lineage_read_is_not_memoized(world, monkeypatch):
    real = plugin._lineages_locked
    calls = []
    def failing(sids):
        calls.append(sids)
        return None
    monkeypatch.setattr(plugin, '_lineages_locked', failing)
    assert plugin._lineages(['c1']) is None
    assert plugin._lineages(['c1']) is None
    assert len(calls) == 2
    monkeypatch.setattr(plugin, '_lineages_locked', real)
    assert plugin._lineages(['c1']) is not None


def test_mid_read_movement_is_not_memoized(world, monkeypatch):
    real = plugin._lineages_locked
    calls = []
    def moving(sids):
        result = real(sids)
        calls.append(sids)
        with sqlite3.connect(world['state']) as conn:
            conn.execute("INSERT INTO messages(session_id, role, content, timestamp, active) VALUES ('c1','user','next',102,1)")
        return result
    monkeypatch.setattr(plugin, '_lineages_locked', moving)
    plugin._lineages(['c1'])
    assert 'lineage_projection' not in plugin._wi
    plugin._lineages(['c1'])
    assert len(calls) == 2
    assert 'lineage_projection' not in plugin._wi
