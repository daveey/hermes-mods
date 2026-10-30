"""A mode=ro open that fails (Apple SQLite, -wal/-shm deleted) must fall back to rw + query_only: reads work, writes fail."""
import sqlite3

import pytest

from plugins.runners import fleet, investigate, receipts
from plugins.runners.tests.test_fleet import NOW, MIN, _shadow
from plugins.runners.tests.test_receipts import OP, make_store, verify


@pytest.fixture
def ro_fails(monkeypatch):
    real = sqlite3.connect

    def connect(target, *a, **kw):
        if 'mode=ro' in str(target):
            raise sqlite3.OperationalError('unable to open database file')
        return real(target, *a, **kw)
    monkeypatch.setattr(sqlite3, 'connect', connect)


@pytest.mark.parametrize('mod', [fleet, investigate, receipts])
def test_helper_reads_but_cannot_write_and_never_creates(mod, tmp_path, ro_fails):
    db = tmp_path / 'x.db'
    c = sqlite3.connect(db); c.execute('create table t(x)'); c.execute('insert into t values (42)'); c.commit(); c.close()
    c = mod._ro_connect(db, timeout=1)
    try:
        assert c.execute('select x from t').fetchall() == [(42,)]
        with pytest.raises(sqlite3.OperationalError):
            c.execute('insert into t values (1)')
    finally:
        c.close()
    with pytest.raises(sqlite3.OperationalError):
        mod._ro_connect(tmp_path / 'missing.db', timeout=1)
    assert not (tmp_path / 'missing.db').exists()


def test_fleet_live_leases_survive_ro_failure(tmp_path, ro_fails):
    _shadow(tmp_path, 'default', [('t_a', 'Card A', 'running', 100, 'hub', NOW + MIN)])
    assert [r['task'] for r in fleet.live_leases(tmp_path, NOW)] == ['t_a']


def test_investigate_latest_survives_ro_failure(tmp_path, ro_fails):
    db = sqlite3.connect(tmp_path / 'default.db')
    db.execute('CREATE TABLE tasks (id TEXT, status TEXT, result TEXT, title TEXT, created_at INTEGER, '
               'completed_at INTEGER, human_request TEXT, idempotency_key TEXT)')
    db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?)',
               ('t_1', 'done', None, investigate.TITLE + ' mini', 5, 6, None, investigate.KEY_PREFIX + 'mini:5'))
    db.commit(); db.close()
    assert investigate.latest([tmp_path])['mini']['task'] == 't_1'


def test_receipts_verify_survives_ro_failure(tmp_path, ro_fails):
    path = make_store(tmp_path)
    assert verify(path).get('reason') != 'unknown_operation'
    assert receipts.verify_receipt('op-missing-x', store_path=path, owner_profile='default', subject_names=()) is not None
