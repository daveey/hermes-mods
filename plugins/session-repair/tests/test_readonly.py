"""The scanner never writes a board or state.db: every connection is query-only, a write attempt fails, files are
byte-identical after a scan that found something on every rule."""
import hashlib
import sqlite3

import pytest

pytest.importorskip('hermes_cli.kanban_db')

from sr_helpers import Env, fixtures  # noqa: E402

F = fixtures()


def digest(path):
    h = hashlib.sha256()
    for suffix in ('', '-wal'):
        try:
            h.update(open(f'{path}{suffix}', 'rb').read())
        except OSError:
            h.update(b'-')
    return h.hexdigest()


def test_a_scan_leaves_every_file_byte_identical(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch)
    F.ready_worker_free(env.conn, env.now)
    F.overdue_clock(env.conn, env.now)
    F.blocked_no_reason(env.conn, env.now)
    F.todo_parents_done(env.conn, env.now)
    F.running_quiet(env.conn, env.now)
    F.review_idle(env.conn, env.now)
    F.missing_parent(env.conn, env.now)
    F.notice_chat(env.state, 'C1', env.now, age_s=3600, closeout={'state': 'error'})
    env.conn.commit()
    env.state.commit()
    before = {p: digest(p) for p in (env.path, tmp_path / 'state.db')}
    changes = env.conn.total_changes
    out = env.scan()
    assert {f['id'] for f in out['findings']} == {
        'BS-ready-worker-free', 'BS-overdue-wake', 'BS-unexplained-wait', 'BS-parent-done', 'BS-stuck-run',
        'BS-review-no-reviewer', 'BS-broken-chain', 'BS-notice-undelivered'}
    assert {p: digest(p) for p in before} == before, 'no byte of any board or state.db changed'
    assert env.conn.total_changes == changes


def test_every_connection_the_scanner_uses_refuses_writes(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch)
    F.overdue_clock(env.conn, env.now)
    src = env.sources()
    for conn in (src.conn('default'), src.state_conn()):
        assert conn.execute('PRAGMA query_only').fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("UPDATE tasks SET title = 'x'" if conn is not src.state_conn() else "DELETE FROM sessions")


def test_a_rule_that_tries_to_write_is_an_error_not_a_write(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch)
    tid = F.overdue_clock(env.conn, env.now)

    def writer(card, src):
        card.conn.execute("UPDATE tasks SET status = 'ready' WHERE id = ?", (card.id,))
    writer.__name__ = 'rule_overdue_wake'  # runs under a real rule id
    monkeypatch.setattr(env.scan_mod, 'CARD_RULES', (writer,) + env.scan_mod.CARD_RULES)
    out = env.scan()
    assert out['error_count'] >= 1 and 'readonly' in out['errors'][0]['error'].lower()
    assert env.conn.execute('SELECT status FROM tasks WHERE id = ?', (tid,)).fetchone()[0] == 'scheduled'
