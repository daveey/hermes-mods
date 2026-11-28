import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('latency_fixture', ROOT/'__init__.py', submodule_search_locations=[str(ROOT)])
pkg = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pkg
spec.loader.exec_module(pkg)
from latency_fixture import collectors, github
from latency_fixture.store import Store


def definition(id, start, end, source='state', **extra):
    return dict(id=id, start=start, end=end, source=source, **extra)


class Collectors(unittest.TestCase):
    def test_state_database_replay_late_results_and_runtime_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'state.db'
            db = sqlite3.connect(path)
            db.executescript('''CREATE TABLE sessions(id TEXT,source TEXT);
                INSERT INTO sessions VALUES ('s','desktop');
                CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,content TEXT,
                tool_call_id TEXT,tool_calls TEXT,tool_name TEXT,timestamp REAL,finish_reason TEXT,
                display_kind TEXT,message_uid TEXT);''')
            def add(id, role, at, content='x', **fields):
                row = dict(id=id, session_id='s', role=role, content=content, timestamp=at, **fields)
                keys = ','.join(row)
                db.execute(f'INSERT INTO messages ({keys}) VALUES ({",".join("?" for _ in row)})', list(row.values()))
                db.commit()
            add(1, 'user', 100)
            add(2, 'assistant', 110, tool_calls=json.dumps([{'id':'c', 'function':{'name':'kanban_show'}}]))
            add(3, 'user', 111, '[runtime]')
            add(4, 'assistant', 120, finish_reason='stop')
            store = Store(Path(tmp)/'series.sqlite')
            defs = [definition('first','typed_user','first_assistant'),definition('final','typed_user','explicit_final'),definition('show','kanban_show','tool_result')]
            self.assertEqual(collectors.collect_state(path,store,defs,0,200),1)
            self.assertEqual(store.view('first',0,200)['p50']//1000,10)
            self.assertEqual(collectors.collect_state(path,store,defs,0,200),0)
            from latency_fixture.registry import fingerprint
            self.assertTrue(store.cursor('state:' + str(path.resolve()) + ':' + fingerprint(defs)))
            add(5, 'tool', 130, tool_call_id='c', tool_name='kanban_show')
            self.assertEqual(collectors.collect_state(path,store,defs,0,200),1)
            self.assertEqual(store.view('show',0,200)['n'],1)
            self.assertEqual(store.view('final',0,200)['n'],0)
            defs.append(definition('first_new','typed_user','first_assistant'))
            self.assertEqual(collectors.collect_state(path,store,defs,0,200),1)
            self.assertEqual(store.view('first_new',0,200)['n'],1)
            db.close()

    def test_board_new_definition_backfills_and_completed_late(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'kanban.db'
            db = sqlite3.connect(path)
            db.executescript('''CREATE TABLE tasks(id TEXT,created_at REAL,completed_at REAL);
                CREATE TABLE task_events(id INTEGER PRIMARY KEY,task_id TEXT,kind TEXT,created_at REAL);
                INSERT INTO tasks VALUES ('t',10,NULL);
                INSERT INTO task_events VALUES (1,'t','claimed',20),(2,'t','decision_requested',30),(3,'t','unblocked',50);''')
            defs = [definition('queue','created','claimed','kanban')]
            store = Store(Path(tmp)/'series.sqlite')
            self.assertEqual(collectors.collect_board(path,store,defs,0,100),1)
            defs.append(definition('decision','decision_requested','unblocked','kanban'))
            from latency_fixture.registry import fingerprint
            self.assertEqual(store.cursor('kanban:'+str(path.resolve())+':'+fingerprint(defs[:-1]))['id'],3)
            self.assertEqual(collectors.collect_board(path,store,defs,0,100),1)
            self.assertEqual(collectors.collect_board(path,store,defs,0,100),0)
            defs.append(definition('done','created','completed','kanban'))
            db.execute('UPDATE tasks SET completed_at=60')
            db.execute("INSERT INTO task_events VALUES (4,'t','completed',60)")
            db.commit()
            self.assertEqual(collectors.collect_board(path,store,defs,0,100),1)
            self.assertEqual(store.view('decision',0,100)['n'],1)
            db.close()

    def test_journal_partial_tail_and_rotation_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp)/'a.jsonl', Path(tmp)/'b.jsonl'
            a.write_text(json.dumps(dict(boot='b',id='r',stage='ingress',utc_ns=100*10**9,mono_ns=10**9,path='/api/board'))+'\n')
            b.write_text(json.dumps(dict(boot='b',id='r',stage='response_end',utc_ns=102*10**9,mono_ns=3*10**9))+'\n'+ '{')
            defs = [definition('board','ingress','response_end','request-stages',path='/api/board')]
            store = Store(Path(tmp)/'series.sqlite')
            self.assertEqual(collectors.collect_journals([a,b],store,defs,200),1)
            self.assertEqual(collectors.collect_journals([a,b],store,defs,200),0)
            self.assertEqual(store.view('board',0,200)['n'],1)

    def test_github_nearest_rank_inputs_and_deploy_ancestry(self):
        run = dict(id=1,path='ci.yml',status='completed',conclusion='success',created_at='2026-01-01T00:00:00Z',run_started_at='2026-01-01T00:00:02Z',updated_at='2026-01-01T00:00:12Z')
        pr = dict(number=2,createdAt='2026-01-01T00:00:00Z',mergedAt='2026-01-01T00:00:20Z',mergeCommit={'oid':'older'},baseRefName='main')
        start = github.epoch(run['created_at'])
        defs = [definition('ci','run_started_at','updated_at',workflow='ci.yml'),definition('deploy','pr_opened','deployed')]
        rows = list(github.github_samples(defs,[run],[pr],{'older':3,'newer':1},[(start+25,1)],'repo',start,start+100))
        self.assertEqual([r['dur_ms'] for r in rows],[10000,25000])
        self.assertEqual(rows[1]['start'],start+20)

    def test_github_saturated_day_split_and_retry(self):
        calls = []
        def fetch(path):
            calls.append(path)
            if len(calls)==1:
                return {'total_count':1000,'workflow_runs':[]}
            return {'total_count':1,'workflow_runs':[{'id':len(calls)}]}
        self.assertEqual(len(github.runs('example/repo',0,86400,fetch)),2)
        self.assertEqual(len(calls),3)
        from types import SimpleNamespace
        attempts, sleeps = [], []
        def run(*args,**kwargs):
            attempts.append(args)
            return SimpleNamespace(returncode=1,stderr='HTTP 503',stdout='') if len(attempts)<3 else SimpleNamespace(returncode=0,stdout='{}',stderr='')
        self.assertEqual(github.api('test',run=run,sleep=sleeps.append),{})
        self.assertEqual(sleeps,[1,2])


if __name__ == '__main__':
    unittest.main()
