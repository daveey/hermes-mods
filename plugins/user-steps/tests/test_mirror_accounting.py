"""Observable contracts for mirror accounting, schema writes and read cleanup."""

import sqlite3
from contextlib import closing, contextmanager

import pytest

from test_kanban_mirror import OWNER, core
from test_kanban_mirror_keyed import keyed, settle  # noqa: F401
from test_withdrawal import ROOT, load_module


def test_skip_is_reported_to_real_core_accounting(keyed):
    # No fake Pass: without the production skip marker the queue still looks right,
    # but the process's real telemetry loses every cheap poll.
    accounting = pytest.importorskip('hermes_cli.loop_accounting')
    k = keyed
    assert isinstance(k.mod['_accounted_pass']('test.probe'), accounting.Pass)
    settle(k)
    name = 'user_steps.mirror_reconcile'
    before = accounting.counters()[name]
    full_before = len(k.passes)
    for _ in range(3):
        k.mirror.reconcile(OWNER)
    after = accounting.counters()[name]
    assert len(k.passes) == full_before
    assert after['runs'] - before['runs'] == 3
    assert after['skips'] - before['skips'] == 3
    for field in ('changes', 'fallbacks', 'rows', 'errors'):
        assert after[field] == before[field], field


def test_schema_setup_is_one_real_write_transaction_per_store_inode(tmp_path, monkeypatch):
    mod = load_module('mirror_schema_accounting', ROOT / 'kanban_mirror.py')
    store = core.ActionStore(tmp_path / 'actions.sqlite3')
    statements = []
    transactions = []
    real_tx = store._tx

    @contextmanager
    def traced_tx():
        transactions.append('begin')
        with real_tx() as conn:
            conn.set_trace_callback(statements.append)
            try:
                yield conn
            finally:
                conn.set_trace_callback(None)

    monkeypatch.setattr(store, '_tx', traced_tx)
    for _ in range(4):
        mod.Mirror(core, store, boards=[])
    assert transactions == ['begin']
    assert sum('CREATE TABLE IF NOT EXISTS kanban_projections' in s for s in statements) == 1
    with store._read() as conn:
        assert conn.execute('SELECT count(*) FROM kanban_projections').fetchone()[0] == 0

    # A new file at the same path must still initialize; a path-only cache is wrong.
    replacement = core.ActionStore(tmp_path / 'replacement.sqlite3')
    replacement.path.replace(store.path)
    mod.Mirror(core, store, boards=[])
    assert transactions == ['begin', 'begin']
    assert sum('CREATE TABLE IF NOT EXISTS kanban_projections' in s for s in statements) == 2
    with store._read() as conn:
        assert conn.execute('SELECT count(*) FROM kanban_projections').fetchone()[0] == 0


@pytest.mark.parametrize('exceptional_exit', [False, True])
def test_kept_reader_releases_explicit_read_snapshot(tmp_path, exceptional_exit):
    # Defensive behavior only: BEGIN is outside _kept's documented autocommit
    # caller contract. This kills M5 but is not a reachable production regression.
    mod = load_module('mirror_read_cleanup', ROOT / 'kanban_mirror.py')
    path = tmp_path / 'reader.sqlite3'
    with closing(sqlite3.connect(path)) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('CREATE TABLE values_seen(value INTEGER)')
        writer.execute('INSERT INTO values_seen VALUES (1)')
        writer.commit()
        try:
            try:
                with mod._kept(path) as reader:
                    reader.execute('BEGIN')
                    assert reader.execute('SELECT value FROM values_seen').fetchone()[0] == 1
                    assert reader.in_transaction
                    if exceptional_exit:
                        raise ValueError('caller failed after read')
            except ValueError:
                assert exceptional_exit
            assert not reader.in_transaction
            writer.execute('UPDATE values_seen SET value=2')
            writer.commit()
            with mod._kept(path) as reused:
                assert reused is reader
                assert reused.execute('SELECT value FROM values_seen').fetchone()[0] == 2
        finally:
            mod._evict()


def test_production_mirror_read_exits_have_no_open_transaction(keyed, monkeypatch):
    # Bounded M5 evidence, not general equivalence: on this ordinary reconcile
    # scenario, rollback's guard is false at every observed reader exit.
    k = keyed
    real_kept = k.mod['_kept']
    states = []

    @contextmanager
    def observed_kept(*args, **kwargs):
        with real_kept(*args, **kwargs) as conn:
            try:
                yield conn
            finally:
                states.append(conn.in_transaction)  # before cleanup, not after it

    monkeypatch.setitem(k.mod, '_kept', observed_kept)
    settle(k)
    k.mirror.reconcile(OWNER)
    assert states and not any(states)
    assert len(k.store.requests(OWNER)) == 1
