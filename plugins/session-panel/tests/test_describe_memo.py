"""The feed's describe memo: reuse only while every input provably holds; any change misses. [daveey]"""
import contextlib
import os
import sqlite3
import time

import pytest

from test_core_readers import api, board, kanban_db, readers_for  # noqa: F401  (fixtures)
from test_reader_batch import graph  # noqa: F401  (fixture)

kas = pytest.importorskip('hermes_cli.kanban_actionability_sources')
REAL_DESCRIBE = kas.describe  # `fresh` reads with it, so its calls are never counted as the feed's


@pytest.fixture
def calls(monkeypatch):
    seen = []
    real = kas.describe

    def counted(conn, tid, **kw):
        seen.append(tid)
        return real(conn, tid, **kw)
    monkeypatch.setattr(kas, 'describe', counted)
    return seen


def _enroll(c, mode='primary'):
    """The board's sync state row, as an enrolled board has it (schema steps only; no capture triggers)."""
    from hermes_cli import kanban_db_sync as sync, kanban_activity_revision as rev
    sync.apply_sync_schema_steps(c)
    path = next(f for _, name, f in c.execute('PRAGMA database_list') if name == 'main')
    rev.ensure_revision(c, rev.tables_for(path))  # its writer re-arms the change triggers for the new tables
    c.execute('DELETE FROM kanban_sync_state')
    c.execute("INSERT INTO kanban_sync_state (board_id, gateway_id, hlc_last, mode) VALUES ('default', 'local-test', '', ?)",
              (mode,))
    c.commit()


def run_pass(r, tid, board='default'):
    """One feed pass reading `tid` (old readers without pass_scope read it plainly)."""
    scope = getattr(r, 'pass_scope', contextlib.nullcontext)
    with scope():
        with r.batch(['S0', 'S1']):
            rec = r.card(tid, board)
    return rec['detail'].get('actionability') if rec else None


def fresh(path, tid):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    try:
        return REAL_DESCRIBE(c, tid, now=0)
    finally:
        c.close()


def test_an_unchanged_card_is_described_once_across_passes(graph, board, calls):
    r, local, _, _ = graph
    first = run_pass(r, local)
    second = run_pass(r, local)
    assert first == second == fresh(board[0], local)
    assert calls.count(local) == 1, calls


def test_a_write_to_the_card_misses(graph, board, calls):
    r, local, _, _ = graph
    path, c = board
    before = run_pass(r, local)
    c.execute("UPDATE tasks SET status='blocked' WHERE id=?", (local,))
    c.commit()
    after = run_pass(r, local)
    assert calls.count(local) == 2
    assert after == fresh(path, local) and after != before


def test_a_write_to_a_parent_misses_and_to_another_card_hits(graph, board, calls):
    r, _, _, _ = graph
    path, c = board
    parent = kanban_db.create_task(c, title='Parent', assignee='default')
    child = kanban_db.create_task(c, title='Child', assignee='default', parents=[parent])
    other = kanban_db.create_task(c, title='Unrelated', assignee='default')
    c.commit()
    run_pass(r, child)
    c.execute('UPDATE tasks SET title=? WHERE id=?', ('Elsewhere', other))
    c.commit()
    run_pass(r, child)
    assert calls.count(child) == 1  # an unrelated write is no input
    c.execute("UPDATE tasks SET status='blocked' WHERE id=?", (parent,))
    c.commit()
    after = run_pass(r, child)
    assert calls.count(child) == 2
    assert after == fresh(path, child)


def test_a_heartbeat_alone_hits_and_stays_equal(graph, board, calls):
    r, local, _, _ = graph
    path, c = board
    run_pass(r, local)
    c.execute("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?, 'heartbeat', '{}', 1)", (local,))
    c.execute('UPDATE tasks SET last_heartbeat_at=123 WHERE id=?', (local,))
    c.commit()
    assert run_pass(r, local) == fresh(path, local)
    assert calls.count(local) == 1


def test_an_untriggered_table_change_misses(graph, board, calls):
    r, local, _, _ = graph
    path, c = board
    run_pass(r, local)
    c.execute('INSERT INTO task_wake_checks (task_id, cmd, every, next_at) VALUES (?, ?, 60, 0)', (local, 'true'))
    c.commit()
    after = run_pass(r, local)
    assert calls.count(local) == 2
    assert after == fresh(path, local)


def test_sync_state_clocks_hit_and_its_mode_misses(graph, board, calls):
    r, local, _, _ = graph
    path, c = board
    _enroll(c)
    clock = next(col for col in ('pulled_at', 'hlc_last', 'cursor')
                 if col in {r[1] for r in c.execute('PRAGMA table_info(kanban_sync_state)')})
    run_pass(r, local)
    c.execute(f'UPDATE kanban_sync_state SET {clock}=?', ('42',))
    c.commit()
    run_pass(r, local)
    assert calls.count(local) == 1
    c.execute("UPDATE kanban_sync_state SET mode='off'")
    c.commit()
    assert run_pass(r, local) == fresh(path, local)
    assert calls.count(local) == 2


def test_a_replaced_board_file_misses(graph, board, calls, tmp_path):
    r, local, _, _ = graph
    path, c = board
    run_pass(r, local)
    copy_path = tmp_path / 'replacement.db'
    dest = sqlite3.connect(copy_path)
    c.backup(dest)
    dest.execute("UPDATE tasks SET status='blocked' WHERE id=?", (local,))
    dest.commit()
    dest.execute('PRAGMA journal_mode=DELETE')
    dest.close()
    assert c.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] == 0
    os.replace(copy_path, path)
    for suffix in ('-wal', '-shm'):
        with contextlib.suppress(FileNotFoundError):
            os.remove(f'{path}{suffix}')
    after = run_pass(r, local)
    assert calls.count(local) == 2
    assert after == fresh(path, local)


def test_a_schema_change_misses(graph, board, calls):
    r, local, _, _ = graph
    path, c = board
    run_pass(r, local)
    c.execute('ALTER TABLE task_wake_checks ADD COLUMN probe_note TEXT')
    c.commit()
    run_pass(r, local)
    assert calls.count(local) == 2


def test_review_config_change_misses(graph, board, calls, monkeypatch):
    r, local, _, _ = graph
    path, c = board
    c.execute("UPDATE tasks SET status='review' WHERE id=?", (local,))
    c.commit()
    setting = {'value': True}
    monkeypatch.setattr(kas, '_review_dispatch', lambda: setting['value'])
    run_pass(r, local)
    run_pass(r, local)
    assert calls.count(local) == 1
    setting['value'] = False
    run_pass(r, local)
    assert calls.count(local) == 2


def _predicate_describe(monkeypatch):
    real = kas.describe

    def with_scan(conn, tid, **kw):
        row = dict(real(conn, tid, **kw))
        # A read selected by predicate, not by the card's ids: a NEW card can start matching it.
        row['zed_cards'] = conn.execute("SELECT count(*) FROM tasks WHERE assignee='zed'").fetchone()[0]
        return row
    monkeypatch.setattr(kas, 'describe', with_scan)


def test_a_new_card_matching_a_predicate_read_misses(graph, board, monkeypatch):
    r, local, _, _ = graph
    path, c = board
    _predicate_describe(monkeypatch)
    assert run_pass(r, local)['zed_cards'] == 0
    kanban_db.create_task(c, title='Newly matching', assignee='zed')
    c.commit()
    assert run_pass(r, local)['zed_cards'] == 1


def test_a_predicate_entry_still_hits_while_its_board_is_untouched(graph, board, monkeypatch):
    r, local, _, _ = graph
    _predicate_describe(monkeypatch)
    seen = []
    real = kas.describe
    monkeypatch.setattr(kas, 'describe', lambda conn, tid, **kw: seen.append(tid) or real(conn, tid, **kw))
    run_pass(r, local)
    run_pass(r, local)
    assert seen.count(local) == 1
    assert r._describe_memo.stats['predicate'] >= 1


def test_the_output_is_clock_free(graph, board, calls, monkeypatch):
    r, local, _, _ = graph
    path, _ = board
    first = run_pass(r, local)
    monkeypatch.setattr(time, 'time', lambda: 4_000_000_000.0)
    assert run_pass(r, local) == first == fresh(path, local)
    assert calls.count(local) == 1

    def no_clock():
        raise AssertionError('describe(now=0) read the clock: memo entries would need an expiry')
    monkeypatch.setattr(time, 'time', no_clock)
    fresh(path, local)


def test_request_threads_and_old_passes_describe_afresh(graph, calls):
    r, local, _, _ = graph
    for _ in range(2):
        with r.batch(['S0', 'S1']):
            r.card(local, 'default')
    assert calls.count(local) == 2


def test_a_reload_starts_empty_and_the_memo_is_bounded(graph, board, api, monkeypatch, calls):
    r, local, _, _ = graph
    path, c = board
    run_pass(r, local)
    reloaded = readers_for(api, path, monkeypatch)
    run_pass(reloaded, local)
    assert calls.count(local) == 2  # a new readers object (plugin reload) has its own, empty memo
    memo = r._describe_memo
    memo.cap = 2
    extra = [kanban_db.create_task(c, title=f'Extra {i}', assignee='default') for i in range(3)]
    c.commit()
    for tid in extra:
        run_pass(r, tid)
    assert len(memo.entries) == 2 and memo.stats['evicted'] >= 2


def test_census_statements_classify(api):
    keyed = 'SELECT * FROM tasks WHERE id=' + "'t_01a2132ee05d1962'"
    assert api._describe_statement(keyed) == ('keyed', ('t_01a2132ee05d1962',))
    assert api._describe_statement("SELECT cmd FROM task_wake_checks WHERE task_id='t_1'")[0] == 'rerun'
    assert api._describe_statement("SELECT title FROM tasks WHERE assignee='zed'")[0] == 'predicate'
    assert api._describe_statement("SELECT title FROM tasks WHERE status='ready' AND id='t_1'")[0] == 'predicate'
    assert api._describe_statement('PRAGMA database_list')[0] == 'schema'


def test_a_replica_parent_change_misses(graph, board, calls, tmp_path, monkeypatch):
    r, _, remote, _ = graph
    path, c = board
    _enroll(c)  # an enrolled board reads a missing parent's status from its replica
    child = kanban_db.create_task(c, title='Waits on a foreign parent', assignee='default')
    c.execute('INSERT INTO task_links (parent_id, child_id) VALUES (?, ?)', (remote, child))
    c.commit()
    terminal = []
    real = kas._replica_parent_terminal
    monkeypatch.setattr(kas, '_replica_parent_terminal', lambda *a: terminal.append(a[1]) or real(*a))
    before = run_pass(r, child)
    assert terminal == [remote]  # the replica was really read
    run_pass(r, child)
    assert calls.count(child) == 1
    rc = sqlite3.connect(tmp_path / 'replica.db')
    rc.execute("UPDATE tasks SET status='done' WHERE id=?", (remote,))
    rc.commit(); rc.close()
    after = run_pass(r, child)
    assert calls.count(child) == 2
    assert after == fresh(path, child) and after != before


def _mid_pass(r, first, mutate, target):
    """A pass whose board refresh already ran (on `first`) when `mutate` lands; then `target` is read."""
    with r.pass_scope():
        with r.batch(['S0', 'S1']):
            r.card(first, 'default')
        mutate()
        with r.batch(['S2', 'S3']):
            return r.card(target, 'default')['detail']['actionability']


def test_a_write_after_the_pass_refresh_misses(graph, board, calls):
    r, local, _, _ = graph
    path, c = board
    other = kanban_db.create_task(c, title='Read first', assignee='default')
    c.commit()
    run_pass(r, local)

    def write():
        c.execute("UPDATE tasks SET status='blocked' WHERE id=?", (local,))
        c.commit()
    after = _mid_pass(r, other, write, local)
    assert calls.count(local) == 2
    assert after == fresh(path, local)


def test_a_file_replaced_after_the_pass_refresh_misses(graph, board, calls, tmp_path):
    """Another file at the same path whose counter happens to vouch for the old entry: only its inode differs."""
    from hermes_cli import kanban_activity_revision as rev
    r, local, _, _ = graph
    path, c = board
    other = kanban_db.create_task(c, title='Read first', assignee='default')
    c.commit()
    run_pass(r, local)
    copy_path = tmp_path / 'other-file.db'
    dest = sqlite3.connect(copy_path)
    c.backup(dest)
    top = dest.execute(f'SELECT revision FROM {rev.REVISION_TABLE} WHERE id=1').fetchone()[0]
    dest.execute("UPDATE tasks SET status='blocked' WHERE id=?", (local,))
    dest.execute(f'DELETE FROM {rev.CHANGE_TABLE} WHERE rev > ?', (top,))
    dest.execute(f'UPDATE {rev.REVISION_TABLE} SET revision=? WHERE id=1', (top,))
    dest.commit()
    dest.execute('PRAGMA journal_mode=DELETE')
    # Same schema cookie as the file it replaces, so neither the schema nor the counter tells them apart.
    dest.execute(f"PRAGMA schema_version={c.execute('PRAGMA schema_version').fetchone()[0]}")
    dest.close()
    assert c.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] == 0

    def replace():
        os.replace(copy_path, path)
        for suffix in ('-wal', '-shm'):
            with contextlib.suppress(FileNotFoundError):
                os.remove(f'{path}{suffix}')
    after = _mid_pass(r, other, replace, local)
    assert calls.count(local) == 2
    assert after == fresh(path, local)
