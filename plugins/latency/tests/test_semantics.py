"""Independent controls for research event/cohort semantics and cache corrections."""
import importlib.util
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('latency_semantics_fixture', ROOT/'__init__.py', submodule_search_locations=[str(ROOT)])
pkg = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pkg
spec.loader.exec_module(pkg)
from latency_semantics_fixture import collectors, github
from latency_semantics_fixture.store import Store


class Semantics(unittest.TestCase):
    def test_board_uses_events_id_order_not_task_timestamps(self):
        tasks = [dict(id='t', created_at=1, completed_at=900), dict(id='missing', created_at=1, completed_at=10)]
        events = [dict(id=i, task_id='t', kind=k, created_at=t) for i,k,t in
                  [(0,'comment',50),(1,'created',100),(2,'claimed',120),(3,'completed',150),(4,'claimed',110),(5,'completed',180)]]
        defs = [dict(id='queue',start='created',end='claimed'),dict(id='done',start='created',end='completed')]
        rows = list(collectors.board_samples(tasks,events,defs))
        self.assertEqual([(r['metric'],r['start'],r['dur_ms']) for r in rows], [('queue',100,20000),('done',100,80000)])

    def test_ci_success_failure_start_day_fallback_latest_run(self):
        midnight = github.epoch('2026-01-02T08:00:00Z')
        row = dict(id=1,run_attempt=1,path='.github/workflows/ci.yml',status='completed',conclusion='success',
                   created_at=github.iso(midnight-10),run_started_at=github.iso(midnight+10),updated_at=github.iso(midnight+30))
        defs = [dict(id='ci',workflow='ci.yml')]
        rows = [row,dict(row,run_attempt=2,updated_at=github.iso(midnight+50)),
                dict(row,id=2,conclusion='cancelled'),dict(row,id=3,conclusion='timed_out'),
                dict(row,id=4,run_started_at=None,created_at=github.iso(midnight+5),conclusion='failure')]
        result = list(github.github_samples(defs,rows,[],{},[],'repo',midnight,midnight+100))
        self.assertEqual([(r['attrs']['run'],r['start'],r['dur_ms']) for r in result], [('1',midnight+10,40000),('4',midnight+5,25000)])

    def test_mutable_projection_migrates_once_replaces_retracts_preserves_other_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp)/'series.sqlite')
            def s(metric,key,start,dur):
                return dict(id=key,metric=metric,start=start,dur_ms=dur,host='h',gateway='g',attrs={})
            store.ingest([s('ci','legacy',10,999),s('span','keep',10,5)],'old',{'at':20})
            rows = [s('ci',str(i),10,i+1) for i in range(25)]
            self.assertEqual(store.reconcile(rows,['ci'],'v2',{'at':30},[r['id'] for r in rows]),25)
            self.assertEqual(store.view('ci',0,100)['n'],25)
            self.assertEqual(store.view('span',0,100)['n'],1)
            store.reconcile([s('ci','24',4000,100)],['ci'],'v2',{'at':40},['24','23'])
            view = store.view('ci',0,5000)
            self.assertEqual(view['n'],24)
            self.assertEqual(len(view['slowest']),20)
            self.assertEqual(view['slowest'][0]['dur_ms'],100)
            self.assertIn('4',[r['id'] for r in view['slowest']])
            store.reconcile([],['ci'],'v2',{'at':50},['24'])
            self.assertEqual(store.view('ci',3600,5000)['n'],0)
            self.assertEqual(store.cursor('v2'),{'at':50})
            # Restart and replay preserve counts and the one-time migration marker.
            Store(store.path).reconcile(rows[:1],['ci'],'v2',{'at':60},['0'])
            self.assertEqual(store.view('ci',0,5000)['n'],23)

    def test_board_revisions_and_multiple_sources_share_migrated_metric(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp)/'series.sqlite')
            defs = [dict(id='done',start='created',end='completed')]
            paths = [Path(tmp)/f'board{i}.db' for i in range(2)]
            for path in paths:
                with closing(sqlite3.connect(path)) as db, db:
                    db.executescript('''CREATE TABLE tasks(id TEXT);
                        CREATE TABLE task_events(id INTEGER PRIMARY KEY,task_id TEXT,kind TEXT,created_at REAL);
                        INSERT INTO tasks VALUES ('t');
                        INSERT INTO task_events VALUES (1,'t','created',100),(2,'t','completed',150);''')
                self.assertEqual(collectors.collect_board(path,store,defs,0,1000),1)
            self.assertEqual(store.view('done',0,1000)['n'],2)
            with closing(sqlite3.connect(paths[0])) as db, db:
                db.execute("INSERT INTO task_events VALUES (3,'t','completed',200)")
            self.assertEqual(collectors.collect_board(paths[0],store,defs,0,1000),1)
            view = store.view('done',0,1000)
            self.assertEqual(view['n'],2)
            self.assertEqual(sorted(r['dur_ms'] for r in view['slowest']),[50000,100000])
            self.assertEqual(collectors.collect_board(paths[0],store,defs,0,1000),0)

    def test_reconcile_transaction_rolls_back_migration_and_retractions(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp)/'series.sqlite')
            row = dict(id='x',metric='ci',start=10,dur_ms=5,host='h',gateway='g')
            store.ingest([row])
            with closing(sqlite3.connect(store.path)) as db, db:
                db.execute("CREATE TRIGGER fail_contribution BEFORE INSERT ON contributions BEGIN SELECT RAISE(ABORT,'fixture'); END")
            with self.assertRaises(sqlite3.IntegrityError):
                store.reconcile([row],['ci'],'v2',{'at':30},['x'])
            self.assertEqual(store.view('ci',0,100)['n'],1)
            self.assertIsNone(store.meta_get('event-semantics-v2:ci'))
            self.assertIsNone(store.cursor('v2'))

    def test_old_completed_run_rerun_is_discovered_by_rotating_sweep(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp)/'series.sqlite')
            defs = [dict(id='ci',source='github.example',start='run_started_at',end='updated_at',workflow='ci.yml')]
            cfg = {'example':{'repo':'example/repo'}}
            row = dict(id=1,run_attempt=1,path='ci.yml',status='completed',conclusion='success',
                       created_at=github.iso(100),run_started_at=github.iso(110),updated_at=github.iso(130))
            with patch.object(github,'runs',return_value=[row]):
                github.collect_github(tmp,store,defs,cfg,0,300000)
            revised = dict(row,run_attempt=2,run_started_at=github.iso(200),updated_at=github.iso(250))
            calls = []
            def runs(repo,a,b,fetch):
                calls.append((a,b))
                return [revised] if a < 100000 else []
            with patch.object(github,'runs',side_effect=runs):
                self.assertEqual(github.collect_github(tmp,store,defs,cfg,0,300600),1)
            self.assertEqual(calls,[(213600,300600),(0,86400)])
            view = store.view('ci',0,300600)
            self.assertEqual(view['n'],1)
            self.assertEqual(view['slowest'][0]['dur_ms'],50000)

    def test_board_recent_event_does_not_retract_retained_old_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp)/'series.sqlite')
            path = Path(tmp)/'board.db'
            day = 86400
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript('''CREATE TABLE tasks(id TEXT);
                    CREATE TABLE task_events(id INTEGER PRIMARY KEY,task_id TEXT,kind TEXT,created_at REAL);
                    INSERT INTO tasks VALUES ('t');
                    INSERT INTO task_events VALUES (1,'t','created',100),(2,'t','completed',3700);''')
            defs = [dict(id='done',start='created',end='completed')]
            collectors.collect_board(path,store,defs,0,5*day)
            self.assertEqual(store.view('done',0,40*day)['n'],1)
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("INSERT INTO task_events VALUES (3,'t','archived',?)",(35*day,))
            collectors.collect_board(path,store,defs,5*day,35*day+1)
            self.assertEqual(store.view('done',0,40*day)['n'],1)
            self.assertEqual(store.view('done',0,40*day)['slowest'][0]['dur_ms'],3600000)

    def test_reconcile_fences_legacy_ingest_and_prunes_contributions(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp)/'series.sqlite')
            rows = [dict(id=str(i),metric='ci',start=100,dur_ms=i+1,host='h',gateway='g') for i in range(25)]
            store.reconcile(rows,['ci'],'v2',{},[])
            # Legacy ingesters must skip both top-20 and non-top-20 identities.
            self.assertEqual(store.ingest(rows),0)
            self.assertEqual(store.view('ci',0,1000)['n'],25)
            store.prune(91*86400)
            with store._connection() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM contributions').fetchone()[0],0)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM identities').fetchone()[0],0)
            self.assertEqual(store.view('ci',0,1000)['n'],0)

    def test_sweep_advances_across_successful_cycles(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp)/'series.sqlite')
            defs = [dict(id='ci',source='github.example',start='run_started_at',end='updated_at',workflow='ci.yml')]
            cfg = {'example':{'repo':'example/repo'}}
            with patch.object(github,'runs',return_value=[]):
                github.collect_github(tmp,store,defs,cfg,0,10*86400)
            calls = []
            def runs(repo,a,b,fetch):
                calls.append((a,b))
                return []
            with patch.object(github,'runs',side_effect=runs):
                github.collect_github(tmp,store,defs,cfg,0,10*86400+600)
                github.collect_github(tmp,store,defs,cfg,0,10*86400+1200)
            self.assertEqual([pair for pair in calls if pair[0]<2*86400],[(0,86400),(86400,2*86400)])

    def test_reconcile_bad_sample_does_not_destroy_legacy_or_advance_cursor(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp)/'series.sqlite')
            row = dict(id='x',metric='ci',start=10,dur_ms=5,host='h',gateway='g')
            store.ingest([row])
            with self.assertRaises(ValueError):
                store.reconcile([dict(row,dur_ms=-1)],['ci'],'v2',{'at':30},['x'])
            self.assertEqual(store.view('ci',0,100)['n'],1)
            self.assertIsNone(store.cursor('v2'))


if __name__ == '__main__':
    unittest.main()
