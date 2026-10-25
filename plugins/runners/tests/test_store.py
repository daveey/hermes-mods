"""SQLite tests use temporary databases and fictional contracts only."""
import copy
import json
import sqlite3
from pathlib import Path

import pytest

FIXTURES = json.loads((Path(__file__).parents[1] / 'contracts/fixtures.json').read_text())
DAY = 86400


def test_wal_persistence_validation_and_transaction_rollback(tmp_path):
    from plugins.runners.store import Store
    from plugins.runners.models import ModelError
    path = tmp_path / 'state.sqlite'
    with Store(path) as store:
        assert store.connection.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        assert path.stat().st_mode & 0o777 == 0o600
        store.put('Runner', FIXTURES['Runner'], at=0)
        assert Path(str(path) + '-wal').stat().st_mode & 0o777 == 0o600
        with pytest.raises(RuntimeError):
            with store.transaction():
                store.put('Host', FIXTURES['Host'], at=0)
                raise RuntimeError('abort')
        assert store.get('Host', FIXTURES['Host']['id']) is None
        with pytest.raises(ModelError):
            store.put('Runner', dict(FIXTURES['Runner'], secret='SECRET'), at=0)
    with Store(path) as store:
        assert store.get('Runner', FIXTURES['Runner']['id']) == FIXTURES['Runner']
        assert store.list('Runner') == [FIXTURES['Runner']]


def test_legacy_backup_migration_and_future_writer_refusal(tmp_path):
    from plugins.runners.store import Store, StoreError, SCHEMA_VERSION
    path = tmp_path / 'state.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE legacy_marker(value TEXT)')
        db.execute("INSERT INTO legacy_marker VALUES ('fictional')")
    with Store(path) as store:
        assert store.connection.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION
        assert store.connection.execute('SELECT value FROM legacy_marker').fetchone()[0] == 'fictional'
        backup = store.backup_path
    assert backup is not None
    with sqlite3.connect(backup) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 0
        assert db.execute('SELECT value FROM legacy_marker').fetchone()[0] == 'fictional'
    with sqlite3.connect(path) as db:
        db.execute(f'PRAGMA user_version={SCHEMA_VERSION + 1}')
    before = path.read_bytes()
    with pytest.raises(StoreError, match='future_schema'):
        Store(path)
    assert path.read_bytes() == before


def test_retention_active_audit_and_transitions(tmp_path):
    from plugins.runners.store import Store
    with Store(tmp_path / 'state.sqlite') as store:
        store.put('Observation', FIXTURES['Observation'], at=0)
        store.put('Transition', FIXTURES['Transition'], key='transition_0000000000000001', at=0)
        store.put('Job', FIXTURES['Job'], at=0)  # queued => active automatically
        closed_job = dict(FIXTURES['Job'], id='job_0000000000000002', status='concluded')
        store.put('Job', closed_job, at=0)
        closed_repair = dict(FIXTURES['Repair'], state='closed')
        store.put('Repair', closed_repair, at=0)
        store.put('Host', FIXTURES['Host'], at=0)
        store.prune(now=31 * DAY)
        assert store.list('Observation') == store.list('Transition') == []
        assert len(store.list('Job')) == 2
        assert store.list('Repair') == [closed_repair]
        store.prune(now=91 * DAY)
        assert store.list('Job') == [FIXTURES['Job']]
        assert store.list('Repair') == []
        assert store.list('Host') == [FIXTURES['Host']]


@pytest.mark.parametrize('lifecycle', ['persistent', 'ephemeral', 'jit'])
def test_retirement_tombstone_and_compaction(tmp_path, lifecycle):
    from plugins.runners.store import Store
    runner = dict(FIXTURES['Runner'], lifecycle=lifecycle)
    observation = dict(FIXTURES['Observation'], entity_id=runner['id'])
    with Store(tmp_path / 'state.sqlite') as store:
        store.put('Runner', runner, at=0)
        store.put('Observation', observation, at=0)
        store.retire_runner(runner['id'], at=DAY)
        tombstone = store.tombstone(runner['id'])
        assert tombstone['id'] == runner['id']
        assert tombstone['lifecycle'] == lifecycle
        assert store.get('Runner', runner['id']) is None
        assert bool(store.list('Observation')) == (lifecycle == 'persistent')
        store.prune(now=30 * DAY)
        assert store.tombstone(runner['id']) is not None
        store.prune(now=32 * DAY)
        assert store.tombstone(runner['id']) is None


def test_nested_transaction_savepoint(tmp_path):
    from plugins.runners.store import Store
    with Store(tmp_path / 'state.sqlite') as store:
        with store.transaction():
            store.put('Runner', FIXTURES['Runner'], at=0)
            with pytest.raises(ValueError):
                with store.transaction():
                    store.put('Host', FIXTURES['Host'], at=0)
                    raise ValueError('abort inner')
            store.put('Provider', FIXTURES['Provider'], at=0)
        assert store.list('Runner') and store.list('Provider')
        assert not store.list('Host')


def test_existing_writer_refuses_future_schema(tmp_path):
    from plugins.runners.store import Store, StoreError, SCHEMA_VERSION
    path = tmp_path / 'state.sqlite'
    with Store(path) as store:
        with sqlite3.connect(path) as db:
            db.execute(f'PRAGMA user_version={SCHEMA_VERSION + 1}')
        with pytest.raises(StoreError, match='future_schema'):
            store.put('Runner', FIXTURES['Runner'], at=0)
        assert store.get('Runner', FIXTURES['Runner']['id']) is None


def test_migration_failure_rolls_back_and_backup_survives(tmp_path):
    from plugins.runners.store import Store
    path = tmp_path / 'state.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE runner_tombstones (old_format TEXT)')
    with pytest.raises(sqlite3.OperationalError):
        Store(path)
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 0
        assert db.execute("SELECT name FROM sqlite_master WHERE name='runner_records'").fetchall() == []
    backups = list(tmp_path.glob('*.bak'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='runner_tombstones'").fetchone()


def test_retention_boundary_and_reads_do_not_renew(tmp_path):
    from plugins.runners.store import Store
    with Store(tmp_path / 'state.sqlite') as store:
        store.put('Observation', FIXTURES['Observation'], at=0)
        store.prune(now=30 * DAY)
        assert store.get('Observation', FIXTURES['Observation']['id']) == FIXTURES['Observation']
        store.prune(now=30 * DAY + 1)
        assert store.list('Observation') == []


def test_active_audit_cannot_be_expired_by_caller(tmp_path):
    from plugins.runners.store import Store
    with Store(tmp_path / 'state.sqlite') as store:
        for name in ['Job', 'Repair', 'Incident']:
            store.put(name, FIXTURES[name], at=0)
        store.prune(now=400 * DAY)
        for name in ['Job', 'Repair', 'Incident']:
            assert store.list(name) == [FIXTURES[name]]


def test_retirement_refuses_running_work_and_preserves_audit(tmp_path):
    from plugins.runners.store import Store, StoreError
    runner = dict(FIXTURES['Runner'], lifecycle='jit')
    job = dict(FIXTURES['Job'], assigned_runner_id=runner['id'])
    with Store(tmp_path / 'state.sqlite') as store:
        store.put('Runner', runner, at=0)
        store.put('Job', job, at=0)
        with pytest.raises(StoreError):
            store.retire_runner(runner['id'], at=DAY)
        assert store.get('Runner', runner['id']) == runner
        store.put('Job', dict(job, status='concluded'), at=DAY)
        store.retire_runner(runner['id'], at=DAY)
        assert len(store.list('Job')) == 1
