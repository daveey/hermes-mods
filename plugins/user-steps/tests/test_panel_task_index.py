"""The shared replica task index: one owner's capture must not pay for the fleet's rows.

Equivalence oracle: the same stores without a ``seq`` column take the full scan.
"""
from contextlib import closing
import json
import sqlite3

import pytest

from test_panel_membership import sources, core, membership, linked, GW, NOW  # noqa: F401
from test_withdrawal import OWNER

SCAN = "SELECT gw,key,data,version FROM replica WHERE coll='tasks'"
OTHER = {**OWNER, 'storedSessionId': 'session-b'}


class Seq:
    """Writes the way hermes-hub does: every replica write takes a new feed seq."""
    def __init__(self, s):
        self.s, self.calls, self.queries = s, [], []
        s.sql('replica', 'ALTER TABLE replica ADD COLUMN seq INTEGER')
        s.sql('replica', 'UPDATE replica SET seq=rowid')
        self.seq = s.sql('replica', 'SELECT coalesce(max(seq),0) FROM replica')[0][0]

    def write(self, record, key=None, gw='remote', version=1, seq=None):
        if seq is None:
            self.seq += 1
            seq = self.seq
        self.s.sql('replica', 'INSERT OR REPLACE INTO replica(gw,coll,key,data,version,seq) VALUES (?,?,?,?,?,?)',
                   (gw, 'tasks', key or 'default/default/' + record['id'], json.dumps(record), version, seq))

    def linked(self, task, gateway, profile):
        self.calls.append(task.get('id'))
        return linked(task, gateway, profile)

    def connect(self, path, **kwargs):
        conn = core._ro_connect(path, **kwargs)
        if path == self.s.paths['replica']:
            conn.set_trace_callback(self.queries.append)
        return conn

    def capture(self, owner=OWNER, **kw):
        s = self.s
        args = dict(gateway=GW, state_path=s.paths['state'], replica_path=s.paths['replica'],
                    ledger_path=s.paths['ledger'], connect=self.connect, step_sql='SELECT NULL WHERE ?=0',
                    chain_cap=1000, linked_sessions=self.linked,
                    validate_binding=lambda binding: None,
                    scope_heartbeats=lambda infos: {(g, sc['profile'], sc['board']): (sc['observed_at'], 450)
                                                    for g, raw in infos for sc in json.loads(raw)['task_scopes']},
                    now=NOW)
        args.update(kw)
        return membership.capture(owner, **args)


def crowd(s, n=120):
    for i in range(n):
        s.put(s.task(f't_crowd{i}', session=f'chat-{i}', title='x' * 40))


def populate(s):
    s.sql('state', "INSERT INTO sessions VALUES ('session-b',NULL,NULL)")
    s.put(s.task(created_at=123))
    s.put(s.task('t_two', title='second'))
    s.put(s.task('t_b', session='session-b'))
    s.obligation()
    # A re-homed publication of a linked card, relevant only by identity.
    s.put(dict(id='t_one', status='blocked', sessions=[], created_at=123), gw='remote', key='other/default/t_one')
    s.sql('replica', 'INSERT INTO replica VALUES (?,?,?,?,1)', ('remote', 'gateway', 'info2', '{}'))
    info = dict(gateway='remote', task_scopes=[dict(profile=p, board='default', state='ok', observed_at=NOW)
                                               for p in ('default', 'other')])
    s.sql('replica', "UPDATE replica SET data=? WHERE coll='gateway' AND key='info'", (json.dumps(info),))
    crowd(s)


def test_index_selects_the_same_rows_as_the_full_scan(sources):
    s = sources
    populate(s)
    scanned = [s.capture(), membership.capture(OTHER, **_args(s))]
    assert all(scanned) and scanned[0] != scanned[1]
    q = Seq(s)
    assert [q.capture(), q.capture(OTHER)] == scanned
    assert SCAN not in q.queries


def _args(s):
    return dict(gateway=GW, state_path=s.paths['state'], replica_path=s.paths['replica'],
                ledger_path=s.paths['ledger'], connect=core._ro_connect, step_sql='SELECT NULL WHERE ?=0',
                chain_cap=1000, linked_sessions=linked, validate_binding=lambda binding: None,
                scope_heartbeats=lambda infos: {(g, sc['profile'], sc['board']): (sc['observed_at'], 450)
                                                for g, raw in infos for sc in json.loads(raw)['task_scopes']},
                now=NOW)


def test_more_owners_and_more_passes_decode_no_unchanged_row(sources):
    """The wake storm: every owner of every pass stepped and decoded every task row."""
    s = sources
    populate(s)
    q = Seq(s)
    total = s.sql('replica', "SELECT count(*) FROM replica WHERE coll='tasks'")[0][0]
    first = q.capture()
    assert first and len(q.calls) == total
    del q.calls[:], q.queries[:]
    for _ in range(5):
        assert q.capture() == first
        assert q.capture(OTHER)
    assert q.calls == []
    assert SCAN not in q.queries
    # No per-row stepping either: a pass reads its owner's few rows, never the crowd.
    assert len(q.queries) < 10 * 30
    # One unrelated write costs one row, not the inventory, and is not this chat's change.
    q.write(s.task('t_crowd7', session='chat-7', title='moved'))
    assert q.capture() == first
    assert q.calls == ['t_crowd7']


def test_writes_between_captures_are_never_swallowed(sources):
    s = sources
    populate(s)
    q = Seq(s)
    seen = [q.capture()]
    def changed():
        value = q.capture()
        assert value is None or value not in seen
        seen.append(value)
        return value
    # An unrelated card gains this chat's link.
    q.write(s.task('t_crowd3'))
    assert changed()
    # A linked card's content moves.
    q.write(s.task('t_two', title='third'))
    assert changed()
    # A linked card leaves.
    s.sql('replica', "DELETE FROM replica WHERE coll='tasks' AND key='default/default/t_crowd3'")
    assert changed()
    # Content rewritten in place under the same seq, version and byte length: relevant
    # rows are always re-read, so the index stamp is never what vouches for them.
    seq = s.sql('replica', "SELECT seq FROM replica WHERE key='default/default/t_two'")[0][0]
    q.write(s.task('t_two', title='fourth'), seq=seq)
    assert changed()
    # Same seq, another length: an unlinked row gains the link.
    seq = s.sql('replica', "SELECT seq FROM replica WHERE key='default/default/t_crowd9'")[0][0]
    q.write(s.task('t_crowd9', title='now linked here'), seq=seq)
    assert changed()
    # Version alone.
    s.sql('replica', "UPDATE replica SET version=7 WHERE key='default/default/t_two'")
    assert changed()
    # A new identity twin of a linked card, itself unlinked.
    restored = seen[-1]
    q.write(dict(id='t_two', status='blocked', sessions=[]), key='default/t_two')
    assert changed() is None
    s.sql('replica', "DELETE FROM replica WHERE key='default/t_two'")
    assert q.capture() == restored


def test_unknown_rows_stay_unknown_and_recover_without_notification(sources):
    s = sources
    populate(s)
    q = Seq(s)
    before = q.capture()
    assert before
    q.write(dict(id='t_crowd5', status='blocked'), key='default/default/elsewhere')
    assert q.capture() is None
    assert q.capture(OTHER) is None
    s.sql('replica', "DELETE FROM replica WHERE key='default/default/elsewhere'")
    assert q.capture() == before
    seq = s.sql('replica', "SELECT seq FROM replica WHERE key='default/default/t_crowd5'")[0][0]
    s.sql('replica', "UPDATE replica SET data='not json', seq=? WHERE key='default/default/t_crowd5'", (seq + 1000,))
    assert q.capture() is None
    q.write(s.task('t_crowd5', session='chat-5', title='x' * 40), seq=seq + 1001)
    assert q.capture() == before


def test_rows_without_seq_take_the_full_scan(sources):
    s = sources
    populate(s)
    scanned = s.capture()
    q = Seq(s)
    s.sql('replica', "UPDATE replica SET seq=NULL WHERE key='default/default/t_crowd1'")
    assert q.capture() == scanned
    assert SCAN in q.queries


def test_each_capture_is_level_with_its_own_snapshot(sources):
    """A reader pinned before a write must not be answered from the index's newer state."""
    s = sources
    populate(s)
    s.sql('replica', 'PRAGMA journal_mode=WAL')
    q = Seq(s)
    before = q.capture()

    class Pinned:
        def __init__(self, conn):
            self.conn = conn
        def execute(self, sql, *args):
            return self.conn.execute('SELECT 1' if sql == 'BEGIN' else sql, *args)
        def close(self):
            pass

    old = core._ro_connect(s.paths['replica'], timeout=.5)
    try:
        old.execute('BEGIN')
        old.execute('SELECT count(*) FROM replica').fetchone()
        q.write(s.task('t_crowd3'))
        after = q.capture()
        assert after and after != before
        normal = q.connect
        q.connect = lambda path, **kw: Pinned(old) if path == s.paths['replica'] else normal(path, **kw)
        assert q.capture() == before
        q.connect = normal
        assert q.capture() == after
    finally:
        old.close()


def test_replaced_replica_file_gets_its_own_index(sources, tmp_path):
    s = sources
    populate(s)
    q = Seq(s)
    before = q.capture()
    assert before
    # Same path, another database: same totals must not be read as the same rows.
    copy = tmp_path / 'copy.db'
    with closing(sqlite3.connect(s.paths['replica'])) as src, closing(sqlite3.connect(copy)) as dst:
        src.backup(dst)
    with closing(sqlite3.connect(copy)) as conn, conn:
        a = json.dumps(s.task('t_two', session='chat-0', title='second'))
        b = json.dumps(s.task('t_crowd0', session=OWNER['storedSessionId'], title='x' * 40))
        conn.execute("UPDATE replica SET data=? WHERE key='default/default/t_two'", (a,))
        conn.execute("UPDATE replica SET data=? WHERE key='default/default/t_crowd0'", (b,))
    copy.replace(s.paths['replica'])
    after = q.capture()
    assert after is None or after != before


@pytest.mark.parametrize('bulk', [0, 10 ** 6])
def test_bulk_and_row_refresh_cover_every_row(sources, monkeypatch, bulk):
    s = sources
    populate(s)
    scanned = s.capture()
    monkeypatch.setattr(membership, '_BULK', bulk)
    q = Seq(s)
    assert q.capture() == scanned
    q.write(s.task('t_crowd3'))
    q.write(s.task('t_two', title='third'))
    changed = q.capture()
    assert changed and changed != scanned
