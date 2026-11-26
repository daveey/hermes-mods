"""Inside a feed pass the board files are stamped once per chunk, not on every card read. [daveey]

Hub 2026-11-26: every card read stat()ed every board file and its shadow (18 paths, two calls each), ~26k stats a
71-chat pass. Outside a pass (request threads, tests) every read still checks for a replaced file at once.
"""
import contextlib
import os

from test_core_readers import api, board, kanban_db, readers_for  # noqa: F401  (fixtures)
from test_reader_batch import graph  # noqa: F401  (fixture)


def _pass(r):
    return getattr(r, 'pass_scope', contextlib.nullcontext)()


def test_a_pass_stamps_the_board_files_once_per_chunk(graph, monkeypatch):
    r, local, _, _ = graph
    stamps = []
    original = r.files
    monkeypatch.setattr(r, 'files', lambda: stamps.append(1) or original())
    with _pass(r):
        with r.batch(['S0', 'S1']):
            for _ in range(5):
                r.card(local, 'default')
        with r.batch(['S2', 'S3']):
            for _ in range(5):
                r.card(local, 'default')
    assert len(stamps) == 2, len(stamps)  # exactly one per chunk: none at all is a failure too


def test_outside_a_pass_every_read_still_checks_for_replacement(graph, monkeypatch):
    r, local, _, _ = graph
    stamps = []
    original = r.files
    monkeypatch.setattr(r, 'files', lambda: stamps.append(1) or original())
    with r.batch(['S0', 'S1']):
        for _ in range(3):
            r.card(local, 'default')
    assert len(stamps) >= 3


def test_a_replaced_board_drops_the_pass_cards_at_the_next_chunk(api, board, monkeypatch, tmp_path):
    from hermes_cli.kanban_db_connect import connect
    path, c = board
    old = kanban_db.create_task(c, title='Old file', session_id='S0', initial_status='running')
    r = readers_for(api, path, monkeypatch)
    fresh = tmp_path / 'replacement.db'
    replacement = connect(fresh)
    new = kanban_db.create_task(replacement, title='New file', session_id='S0', initial_status='running')
    assert c.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] == 0
    assert replacement.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] == 0
    replacement.close()
    try:
        with _pass(r):
            with r.batch(['S0', 'S1']):
                assert r.card(old, 'default') is not None
            os.replace(fresh, path)
            with r.batch(['S2', 'S3']):
                assert r.card(old, 'default') is None
                assert r.card(new, 'default') is not None
    finally:
        c.close(); r.release()
