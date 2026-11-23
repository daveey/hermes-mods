"""Forecast model: burn rate from history, refills at resets, run-out time, learning state, REST shape."""
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_here = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('account_usage_forecast_t', _here / 'forecast.py')
fc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fc)

NOW = 1_800_000_000.0
H = 3600.0


def iso(ts):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def acct(label, state='ready', used=None, week_reset=None, back=None):
    return {'label': label, 'state': state, 'week_used': used,
            'week_resets_at': iso(week_reset) if week_reset else None, 'resets_at': iso(back) if back else None}


class BurnRateTests(unittest.TestCase):
    def test_sums_increases_per_account_over_the_span(self):
        s = [(NOW - 10 * H, 'a', 10.0, 5e9), (NOW - 5 * H, 'a', 30.0, 5e9), (NOW, 'a', 40.0, 5e9),
             (NOW - 10 * H, 'b', 0.0, 6e9), (NOW, 'b', 20.0, 6e9)]
        rate, span = fc.burn_rate(s, NOW)
        self.assertEqual(span, 10 * H)
        self.assertAlmostEqual(rate, (30 + 20) / 10)

    def test_reset_starts_a_new_window_and_drops_are_ignored(self):
        s = [(NOW - 4 * H, 'a', 90.0, NOW - 2 * H), (NOW - 1 * H, 'a', 6.0, NOW + 166 * H)]
        rate, _ = fc.burn_rate(s, NOW)
        self.assertAlmostEqual(rate, 6 / 4)

    def test_short_history_is_learning(self):
        rate, span = fc.burn_rate([(NOW - H, 'a', 1.0, None), (NOW, 'a', 5.0, None)], NOW)
        self.assertIsNone(rate)
        self.assertEqual(span, H)


class ProjectTests(unittest.TestCase):
    def norm(self, *a):
        return fc.normalize(a)

    def test_runs_out_then_refills_at_a_spent_accounts_reset(self):
        accts = self.norm(acct('a', used=80, week_reset=NOW + 100 * H), acct('b', 'limited', back=NOW + 30 * H))
        p = fc.project(accts, 2.0, NOW)  # 20 left at 2/h -> dry at hour 10, refill at 30
        self.assertEqual(p['left'], 20.0)
        self.assertEqual(p['gaps'][0], {'start_h': 10, 'end_h': 30})
        self.assertEqual(p['curve'][30], 100.0)

    def test_ready_account_gets_its_used_share_back_at_its_weekly_reset(self):
        p = fc.project(self.norm(acct('a', used=60, week_reset=NOW + 5 * H)), 0.0, NOW)
        self.assertEqual(p['curve'][4], 40.0)
        self.assertEqual(p['curve'][5], 100.0)
        self.assertEqual(p['gaps'], [])

    def test_session_limited_account_returns_only_its_weekly_remainder(self):
        accts = self.norm(acct('a', 'limited', used=70, week_reset=NOW + 50 * H, back=NOW + 3 * H))
        p = fc.project(accts, 0.0, NOW)
        self.assertEqual((p['curve'][2], p['curve'][3], p['curve'][50]), (0.0, 30.0, 100.0))

    def test_disabled_accounts_are_ignored(self):
        accts = fc.normalize([acct('a', used=50), {**acct('b', 'limited', back=NOW + H), 'disabled': True}])
        self.assertEqual(len(accts), 1)


class ForecastTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='au-forecast-')
        self.home = Path(self.tmp.name)
        fc._last_record.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def test_learning_until_two_hours_of_history_then_a_verdict(self):
        accts = [acct('x@example.com', used=50, week_reset=NOW + 150 * H)]
        out = fc.forecast(self.home, 'anthropic', accts, NOW)
        self.assertEqual(out['verdict'], 'learning')
        self.assertEqual(out['left'], 50.0)
        line = json.loads(fc.history_path(self.home).read_text().splitlines()[0])
        self.assertNotIn('example.com', json.dumps(line))  # labels are hashed
        accts = [acct('x@example.com', used=60, week_reset=NOW + 150 * H)]
        out = fc.forecast(self.home, 'anthropic', accts, NOW + 3 * H)
        self.assertEqual(out['verdict'], 'short')  # 40 left at 10/3 per h: dry in 12 h, no reset before 147 h
        self.assertEqual(out['runout_h'], 12)

    def test_near_miss_before_a_reset_is_tight(self):
        # 2 h of history at 5/h; 50 left lasts 10 h; nine spent accounts are back at 9 h -> 5 left at the low point,
        # while the week's pace (5/h * 168 h over 50 + 900 + 50 supply) stays under 90%.
        fc.forecast(self.home, 'openai-codex', [acct('x', used=40, week_reset=NOW + 150 * H)], NOW - 2 * H)
        spent = [acct(f'y{i}', 'limited', back=NOW + 9 * H) for i in range(9)]
        out = fc.forecast(self.home, 'openai-codex', [acct('x', used=50, week_reset=NOW + 150 * H), *spent], NOW)
        self.assertEqual(out['gaps'], [])
        self.assertLess(out['pace'], fc.TIGHT_PACE)
        self.assertEqual(out['verdict'], 'tight')

    def test_records_are_throttled_per_provider(self):
        a = [acct('x', used=1)]
        fc.forecast(self.home, 'anthropic', a, NOW)
        fc.forecast(self.home, 'anthropic', a, NOW + 60)
        fc.forecast(self.home, 'openai-codex', a, NOW + 60)
        self.assertEqual(len(fc.history_path(self.home).read_text().splitlines()), 2)

    def test_merges_core_pacing_history_read_only(self):
        db = sqlite3.connect(self.home / fc.PACING_DB)
        db.execute('CREATE TABLE samples (ts REAL, provider TEXT, account_hash TEXT, kind TEXT, used REAL, reset_at REAL)')
        h = fc.account_hash('x')
        db.executemany('INSERT INTO samples VALUES (?,?,?,?,?,?)', [
            (NOW - 10 * H, 'openai-codex', h, 'seven_day', 10.0, NOW + 150 * H),
            (NOW - 10 * H, 'openai-codex', h, 'five_hour', 99.0, NOW + 1 * H),
            (NOW, 'openai-codex', h, 'seven_day', 15.0, NOW + 150 * H)])
        db.commit(); db.close()
        out = fc.forecast(self.home, 'openai-codex', [acct('x', used=15, week_reset=NOW + 150 * H)], NOW)
        self.assertEqual(out['verdict'], 'ok')
        self.assertAlmostEqual(out['burn_per_h'], 0.5)
        self.assertEqual(out['history_h'], 10.0)

    def test_pacing_wal_fallback_reads_without_allowing_writes(self):
        path = self.home / fc.PACING_DB
        with sqlite3.connect(path) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE samples (ts, provider, account_hash, kind, used, reset_at)')
            db.execute('INSERT INTO samples VALUES (?,?,?,?,?,?)',
                       (NOW, 'openai-codex', 'hashed', 'seven_day', 15.0, None))
        db.close()
        self.assertFalse(Path(str(path) + '-wal').exists())
        connect = sqlite3.connect
        attempts = []

        def missing_sidecars(database, **kwargs):
            attempts.append(database)
            if database.endswith('mode=ro'):
                raise sqlite3.OperationalError('unable to open database file')
            return connect(database, **kwargs)

        with mock.patch.object(fc.sqlite3, 'connect', side_effect=missing_sidecars):
            self.assertEqual(fc.read_samples(self.home, 'openai-codex', NOW),
                             [(NOW, 'hashed', 15.0, None)])
            with fc._ro_connect(path) as conn:
                self.assertEqual(conn.execute('PRAGMA query_only').fetchone(), (1,))
                with self.assertRaises(sqlite3.OperationalError):
                    conn.execute('DELETE FROM samples')
            conn.close()
        self.assertEqual(attempts, [f'file:{path}?mode=ro', f'file:{path}?mode=rw'] * 2)

    def test_pacing_fallback_does_not_create_a_missing_database(self):
        path = self.home / fc.PACING_DB
        with self.assertRaises(sqlite3.OperationalError):
            fc._ro_connect(path)
        self.assertFalse(path.exists())
        self.assertEqual(fc.read_samples(self.home, 'openai-codex', NOW), [])

    def test_old_lines_are_pruned_after_eight_days(self):
        path = fc.history_path(self.home)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'ts': NOW - 9 * 24 * H, 'p': 'anthropic', 'a': 'z', 'u': 1, 'r': None}) + '\n')
        fc._last_prune.clear()
        fc.forecast(self.home, 'anthropic', [acct('x', used=1)], NOW)
        self.assertEqual(len(path.read_text().splitlines()), 1)


class RestTests(unittest.TestCase):
    def test_forecast_endpoint_shape(self):
        try:
            _api = importlib.util.spec_from_file_location('account_usage_plugin_api_fc', _here / 'dashboard' / 'plugin_api.py')
            api = importlib.util.module_from_spec(_api)
            _api.loader.exec_module(api)
        except ImportError as exc:  # fastapi/pydantic absent (core-free CI)
            self.skipTest(str(exc))
        with tempfile.TemporaryDirectory(prefix='au-forecast-api-') as d:
            body = api.ForecastBody(providers=[{'slug': 'anthropic', 'accounts': [acct('x', used=20)]}])
            out = api.forecasts(body, Path(d), NOW)
            self.assertEqual(out['anthropic']['verdict'], 'learning')
            self.assertEqual(out['anthropic']['left'], 80.0)
            self.assertEqual(len(out['anthropic']['curve']), fc.HORIZON_H + 1)


class PacingTransferTests(unittest.TestCase):
    """The pacing-history read returns the plain query's values while transferring one SQLite row into Python
    (each row a cursor returns re-takes the GIL: up to 5 ms apiece inside the busy Desktop backend)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        import random
        rnd = random.Random(3)
        self.rows = []
        for i in range(3000):
            reset = rnd.choice([None, NOW + rnd.random() * 150 * H, int(NOW + 100 * H)])
            self.rows.append((NOW - rnd.random() * 30 * H, rnd.choice(['anthropic', 'openai-codex']),
                              f'h{i % 7}"雪', rnd.choice(['seven_day', 'five_hour']), rnd.random() * 100 / 3, reset))
        with sqlite3.connect(self.home / fc.PACING_DB) as db:
            db.execute('CREATE TABLE samples (ts REAL NOT NULL, provider TEXT NOT NULL, account_hash TEXT NOT NULL, '
                       'kind TEXT NOT NULL, used REAL NOT NULL, reset_at)')
            db.executemany('INSERT INTO samples VALUES (?,?,?,?,?,?)', self.rows)
        db.close()

    def plain(self, provider, since):
        with sqlite3.connect(self.home / fc.PACING_DB) as db:
            rows = db.execute('SELECT ts, account_hash, used, reset_at FROM samples WHERE provider=? AND kind=? '
                              'AND ts>=?', (provider, fc.WEEK_KIND, since)).fetchall()
        db.close()
        return [(float(t), a, float(u), r) for t, a, u, r in rows]

    def test_same_samples_one_transferred_row(self):
        connect = sqlite3.connect
        transferred = []

        def counting(*a, **kw):
            conn = connect(*a, **kw)
            conn.row_factory = lambda cursor, row: transferred.append(row) or row
            return conn

        since = NOW - 24 * H
        expected = self.plain('openai-codex', since)
        self.assertGreater(len(expected), 500)
        with mock.patch.object(fc.sqlite3, 'connect', side_effect=counting):
            got = fc.read_samples(self.home, 'openai-codex', since)
        self.assertEqual(got, expected)
        self.assertEqual([tuple(map(type, r)) for r in got], [tuple(map(type, r)) for r in expected])
        # query_only PRAGMA returns no row; the samples cross as one batched row.
        self.assertEqual(len(transferred), 1)

    def test_a_real_without_an_exact_scale_reads_row_by_row(self):
        with sqlite3.connect(self.home / fc.PACING_DB) as db:
            db.execute("INSERT INTO samples VALUES (?, 'anthropic', 'h', 'seven_day', 1.0, 1e300)", (NOW - H,))
        db.close()
        since = NOW - 24 * H
        expected = self.plain('anthropic', since)
        self.assertIn(1e300, [r[3] for r in expected])
        with sqlite3.connect(self.home / fc.PACING_DB) as db:
            self.assertIsNone(db.execute(fc._batch_query(
                'SELECT ts, account_hash, used, reset_at FROM samples', 4)).fetchone()[0])
        db.close()
        self.assertEqual(fc.read_samples(self.home, 'anthropic', since), expected)


if __name__ == '__main__':
    unittest.main()
