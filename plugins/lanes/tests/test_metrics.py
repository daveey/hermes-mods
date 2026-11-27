"""Spend windows use the same receipt deduplication as lanes flow."""
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace


def load():
    spec = importlib.util.spec_from_file_location('lane_metrics_test', Path(__file__).parents[1] / 'dashboard/metrics.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_spend_receipt_cutoffs_and_deduplication():
    metrics = load()
    pol = SimpleNamespace(lanes={'work': {}, 'idle': {}}, lane_of=lambda _: 'work')
    now = 1_000_000
    with closing(sqlite3.connect(':memory:')) as db:
        db.executescript('CREATE TABLE tasks(id TEXT,tenant TEXT); '
                         'CREATE TABLE task_events(task_id TEXT,run_id INTEGER,kind TEXT,created_at INTEGER,payload TEXT);')
        db.execute("INSERT INTO tasks VALUES ('t_1','work')")
        def seed(run, age, cost, kind='worker_result'):
            db.execute('INSERT INTO task_events VALUES (?,?,?,?,?)',
                       ('t_1', run, kind, now-age, json.dumps({'usage': {'cost_usd': cost}})))
        seed(1, 3600, 4)  # exact hour boundary excluded from hour
        seed(1, 3601, 9)  # same run higher cost outside the hour
        seed(1, 10, 3)
        seed(2, 86400, 5)
        seed(3, 604800, 99)  # week boundary excluded entirely
        seed(4, 604799, 6)
        seed(5, 2, 99, 'heartbeat')
        value = metrics.spend(db, pol, now=now)
        assert value['work'] == dict(hour=3., day=9., week=20.)
        assert value['idle'] == dict(hour=0., day=0., week=0.)
        for key, seconds in [('hour', 3600), ('day', 86400), ('week', 604800)]:
            expected = db.execute(
                "SELECT SUM(cost) FROM (SELECT MAX(COALESCE(json_extract(payload,'$.usage.cost_usd'),0)) cost "
                "FROM task_events WHERE kind='worker_result' AND created_at>? "
                "AND json_extract(payload,'$.usage') IS NOT NULL GROUP BY task_id,run_id)", (now-seconds,)).fetchone()[0]
            assert value['work'][key] == expected


def test_merge_known_and_missing_board_metrics():
    metrics = load()
    def row(running, state='partial', rejected=0):
        return dict(ticks=dict(window='24h', bucket_s=1800, cap=2,
                               cells=[dict(t=100, running=running, cap=2, state=state, rejected=rejected)]),
                    spend=dict(hour=1., day=2., week=3.))
    merged = metrics.merge({'work': row(1)}, {'work': row(1, 'rejecting', 2)})['work']
    assert merged['ticks']['cells'][0] == dict(t=100, running=2, cap=2, state='rejecting', rejected=2)
    assert merged['spend'] == dict(hour=2., day=4., week=6.)
    unknown = metrics.merge({'work': row(1)}, {'work': dict(ticks=None, spend=None)})['work']
    assert unknown == dict(ticks=None, spend=None)
    unknown = metrics.merge({'work': row(None, 'unknown', None)}, {'work': row(1)})['work']
    assert unknown['ticks']['cells'][0]['state'] == 'unknown'
    assert unknown['ticks']['cells'][0]['running'] is None


def test_projection_keeps_metrics_in_collapsed_and_expanded_views():
    spec = importlib.util.spec_from_file_location('lane_metrics_view', Path(__file__).parents[1] / 'dashboard/view.py')
    view = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(view)
    snap = {'lanes': {'work': dict(ticks=[dict(t=1, state='rejecting', running=1, cap=1, rejected=1)],
                                       spend=dict(hour=1., day=2., week=3.))}}
    raw = {'cards': [], 'lanes': {'work': {'limits': {'max_workers': 1}}}}
    for expanded in (False, True):
        row = view.project(snap, raw, expanded)['lanes']['work']
        assert row['ticks'] == snap['lanes']['work']['ticks']
        assert row['spend'] == snap['lanes']['work']['spend']
        assert row['limits']['max_workers'] == 1
        missing = view.project(snap, {**raw, 'summary_complete': False}, expanded)['lanes']['work']
        assert missing['ticks'] is None and missing['spend'] is None
    older = view.project({'lanes': {'work': {}}}, raw, False)['lanes']['work']
    assert older['ticks'] is None and older['spend'] is None
