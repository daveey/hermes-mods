"""Runner detail history is index-backed; investigation contexts stay one per entity.

Fictional ids only. Seeds a temporary database, never the live one.
"""
import json
import random
import sqlite3
from pathlib import Path

import pytest

F = json.loads((Path(__file__).parents[1] / 'contracts/fixtures.json').read_text())
DAY = 86400


def _hex(n, width=32):
    return format(n, 'x').rjust(width, '0')


def _observation(n, entity):
    return dict(F['Observation'], id='obj_' + _hex(n), entity_id=entity)


def _incident(n, entity, dimension='investigation', transitions=()):
    return dict(F['Incident'], id='incident_' + _hex(n), entity_id=entity, dimension=dimension,
                transitions=list(transitions))


def config():
    from plugins.runners.config import normalize
    return normalize(dict(schema_version=1, owner_gateway_id=F['Owner']['gateway_id'], owner_profile='example', scopes=[F['Scope']]))


class Adapter:
    def __init__(self):
        from plugins.runners.providers.base import ProviderResult
        self.result = ProviderResult((F['Runner'],), True, None, 1000, 1)

    def enumerate(self, scope):
        return self.result


def _plan(db, sql, args):
    return ' | '.join(row[-1] for row in db.execute('EXPLAIN QUERY PLAN ' + sql, args))


@pytest.mark.parametrize('kind', ['Observation', 'Incident'])
def test_detail_history_queries_use_entity_index_with_same_order_and_limit(tmp_path, kind):
    from plugins.runners.store import Store
    from plugins.runners import service
    sql = service.OBSERVATION_HISTORY_SQL if kind == 'Observation' else service.INCIDENT_HISTORY_SQL
    path = tmp_path / 'state.sqlite'
    entities = ['runner_' + _hex(i, 16) for i in range(5)]
    rng = random.Random(7)
    stamps = rng.sample(range(1, 100000), 600)
    expected = {}
    with Store(path) as store, store.transaction():
        for n, stamp in enumerate(stamps):
            entity = entities[n % len(entities)]
            doc = _observation(n, entity) if kind == 'Observation' else _incident(n, entity)
            store.put(kind, doc, at=stamp)
            expected.setdefault(entity, []).append((stamp, doc))
    db = service.connect(path)
    try:
        plan = _plan(db, sql, (entities[0], 50))
        assert 'runner_entity' in plan, plan
        assert 'TEMP B-TREE' not in plan, plan
        for entity in entities:
            for limit in (0, 1, 50, 100, 1000):
                got = [json.loads(r[0]) for r in db.execute(sql, (entity, limit))]
                want = [doc for _, doc in sorted(expected[entity], key=lambda p: -p[0])][:limit]
                assert got == want
    finally:
        db.close()


def test_inspect_returns_newest_history_first_bounded_by_limit(tmp_path):
    """The Service.inspect entry path: newest first, history_limit honoured, other entities excluded."""
    from plugins.runners.collector import Collector
    from plugins.runners.service import Service
    from plugins.runners.store import Store
    path = tmp_path / 'state.sqlite'
    rid = F['Runner']['id']
    with Store(path) as store:
        Collector(store, config(), Adapter(), clock=lambda: 1000).tick()
        with store.transaction():
            for n in range(40):
                store.put('Observation', _observation(1000 + n, rid), at=2000 + n)
                store.put('Observation', _observation(5000 + n, 'runner_' + _hex(77, 16)), at=9000 + n)
    detail = Service(path, config(), clock=lambda: 1001).inspect(rid, 7)['data']
    assert [o['id'] for o in detail['observations']] == ['obj_' + _hex(1000 + n) for n in range(39, 32, -1)]
    assert [i['entity_id'] for i in detail['incidents']] == [rid]


def _v1_database(path, rows, repairs=()):
    """The exact schema-v1 layout shipped before the entity index."""
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE runner_records (
            kind TEXT NOT NULL, record_key TEXT NOT NULL, payload TEXT NOT NULL,
            updated_at REAL NOT NULL, active INTEGER NOT NULL CHECK(active IN (0,1)),
            PRIMARY KEY(kind,record_key))''')
        db.execute('CREATE INDEX runner_retention ON runner_records(kind,active,updated_at)')
        db.execute('CREATE TABLE runner_tombstones (record_key TEXT PRIMARY KEY, payload TEXT NOT NULL, retired_at REAL NOT NULL)')
        db.execute('CREATE TABLE health_states (runner_id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        for kind, doc, at, active in rows:
            db.execute('INSERT INTO runner_records VALUES (?,?,?,?,?)', (kind, doc['id'], json.dumps(doc), at, active))
        if repairs:
            db.execute('CREATE TABLE repair_domain_records (id TEXT PRIMARY KEY, scope TEXT, principal TEXT, owner TEXT, payload TEXT, brief TEXT, active INTEGER DEFAULT 0)')
            for n, incident_id in enumerate(repairs):
                db.execute('INSERT INTO repair_domain_records(id,scope,principal,owner,payload,brief) VALUES (?,?,?,?,?,?)',
                           ('repair_' + _hex(n), 'runner:x', 'p', 'o', json.dumps(dict(incident_id=incident_id)), '{}'))
        db.execute('PRAGMA user_version=1')


def test_v1_migration_adds_index_and_resolves_only_duplicate_empty_investigation_contexts(tmp_path, monkeypatch):
    from plugins.runners import store as store_module
    from plugins.runners.store import Store, SCHEMA_VERSION
    path = tmp_path / 'state.sqlite'
    a, b, c = ('runner_' + _hex(i, 16) for i in (1, 2, 3))
    dup = [_incident(i, a) for i in range(10, 15)]           # 5 open contexts for one entity
    kept_newest = dup[3]
    referenced = dup[0]                                       # named by a repair: never resolved
    with_evidence = _incident(20, a, transitions=[F['Transition']])
    other_dimension = _incident(21, a, dimension='registration')
    single = _incident(30, b)
    resolved_before = dict(_incident(40, c), resolved_at='2030-01-01T00:00:00Z')
    observation = _observation(50, a)
    rows = [('Incident', d, at, 1) for d, at in zip(dup, (100, 200, 300, 900, 400))]
    rows += [('Incident', with_evidence, 50, 1), ('Incident', other_dimension, 60, 1),
             ('Incident', single, 70, 1), ('Incident', resolved_before, 80, 0),
             ('Observation', observation, 90, 0)]
    _v1_database(path, rows, repairs=[referenced['id']])
    migrated_at = 10 * DAY
    monkeypatch.setattr(store_module.time, 'time', lambda: migrated_at)
    with Store(path) as store:
        assert SCHEMA_VERSION == 2
        assert store.connection.execute('PRAGMA user_version').fetchone()[0] == 2
        assert store.backup_path is not None and store.backup_path.exists()
        names = {r[0] for r in store.connection.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert {'runner_retention', 'runner_entity'} <= names
        state = {r[0]: (json.loads(r[1]), r[2], r[3]) for r in store.connection.execute(
            "SELECT record_key,payload,updated_at,active FROM runner_records WHERE kind='Incident'")}
        open_ids = {k for k, (_, _, active) in state.items() if active}
        assert open_ids == {kept_newest['id'], referenced['id'], with_evidence['id'], other_dimension['id'], single['id']}
        original = dict(zip((d['id'] for d in dup), (100, 200, 300, 900, 400)))
        for doc in dup:
            payload, updated, active = state[doc['id']]
            if doc['id'] in open_ids:
                assert payload == doc and active == 1
            else:
                assert payload == dict(doc, resolved_at='1970-01-11T00:00:00Z')
                # Not a put: the last-put timestamp (retention clock, history order) is kept.
                assert (updated, active) == (original[doc['id']], 0)
        assert state[resolved_before['id']] == (resolved_before, 80, 0)
        assert store.get('Observation', observation['id']) == observation
        # Newest-first detail history still leads with the open context.
        from plugins.runners.service import INCIDENT_HISTORY_SQL
        first = json.loads(store.connection.execute(INCIDENT_HISTORY_SQL, (a, 1)).fetchone()[0])
        assert first == kept_newest
        # Resolved empty duplicates fall under the 1-day churn bound (Store.prune
        # churn_days), counted from the kept last put, no sooner.
        store.prune(now=400 + DAY)
        left = {r[0] for r in store.connection.execute("SELECT record_key FROM runner_records WHERE kind='Incident'")}
        assert left == open_ids | {dup[4]['id']}
        store.prune(now=400 + DAY + 1)
        left = {r[0] for r in store.connection.execute("SELECT record_key FROM runner_records WHERE kind='Incident'")}
        assert left == open_ids
    with sqlite3.connect(store.backup_path) as backup:
        assert backup.execute('PRAGMA user_version').fetchone()[0] == 1
        assert backup.execute("SELECT count(*) FROM runner_records WHERE kind='Incident' AND active=1").fetchone()[0] == 8


def test_collector_reuses_investigation_context_beyond_first_thousand_incidents(tmp_path):
    """list('Incident') caps at 1000 rows; the context lookup must not, or every tick mints a new one."""
    from plugins.runners.collector import Collector
    from plugins.runners.store import Store
    path = tmp_path / 'state.sqlite'
    adapter = Adapter()
    with Store(path) as store:
        Collector(store, config(), adapter, clock=lambda: 1000).tick()
        with store.transaction():
            for n in range(1001):
                # Keys sort before any uuid4 context id the collector minted.
                store.put('Incident', _incident(n, 'runner_' + _hex(n + 100, 16)), at=10)
        for at in (1031, 1062):
            Collector(store, config(), adapter, clock=lambda at=at: at).tick()
        mine = [r for r in store.connection.execute(
            "SELECT record_key FROM runner_records WHERE kind='Incident' AND active=1 AND json_extract(payload,'$.entity_id')=?",
            (F['Runner']['id'],))]
        assert len(mine) == 1


def test_collector_prunes_on_schedule_with_existing_policy(tmp_path):
    from plugins.runners.collector import Collector
    from plugins.runners.store import Store
    path = tmp_path / 'state.sqlite'
    clock = [40 * DAY]
    ticks = [100.0]
    with Store(path) as store:
        store.put('Observation', _observation(1, 'runner_' + _hex(9, 16)), at=1 * DAY)    # 39 days old
        store.put('Observation', _observation(2, 'runner_' + _hex(9, 16)), at=39 * DAY)   # 1 day old
        c = Collector(store, config(), Adapter(), clock=lambda: clock[0], monotonic=lambda: ticks[0])
        c.tick()
        assert store.get('Observation', 'obj_' + _hex(1)) is None
        assert store.get('Observation', 'obj_' + _hex(2)) is not None
        # Not again within the hour: a second stale row survives until the next window.
        store.put('Observation', _observation(3, 'runner_' + _hex(9, 16)), at=1 * DAY)
        clock[0] += 30; ticks[0] += 30
        c.tick()
        assert store.get('Observation', 'obj_' + _hex(3)) is not None
        clock[0] += 3600; ticks[0] += 3600
        c.tick()
        assert store.get('Observation', 'obj_' + _hex(3)) is None


def test_collector_prune_uses_configured_retention(tmp_path):
    """Config retention reaches Store.prune; raw Observations default to 2 days."""
    from plugins.runners.collector import Collector
    from plugins.runners.config import normalize
    from plugins.runners.store import Store
    now = 40 * DAY
    entity = 'runner_' + _hex(9, 16)
    with Store(tmp_path / 'default.sqlite') as store:
        store.put('Observation', _observation(1, entity), at=now - 3 * DAY)
        store.put('Observation', _observation(2, entity), at=now - 1.5 * DAY)
        Collector(store, config(), Adapter(), clock=lambda: now).tick()
        assert store.get('Observation', 'obj_' + _hex(1)) is None
        assert store.get('Observation', 'obj_' + _hex(2)) is not None
    short = normalize(dict(schema_version=1, owner_gateway_id=F['Owner']['gateway_id'], owner_profile='example',
                           scopes=[F['Scope']], retention=dict(observations_days=1)))
    with Store(tmp_path / 'short.sqlite') as store:
        store.put('Observation', _observation(2, entity), at=now - 1.5 * DAY)
        Collector(store, short, Adapter(), clock=lambda: now).tick()
        assert store.get('Observation', 'obj_' + _hex(2)) is None
