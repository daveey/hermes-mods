"""Real child request counts with fake git/gh and isolated HOME; no network."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('polling_publisher', HERE / 'skill_publish.py')
assert spec is not None and spec.loader is not None
pub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pub)


class PollingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.root = self.home / 'repo'
        self.root.mkdir()
        (self.root / '.git').mkdir()
        self.bin = self.home / 'bin'
        self.bin.mkdir()
        self.log = self.home / 'gh.log'
        self.state = self.home / 'state'
        self.state.write_text('pending')
        self.sha = 'a' * 40
        self.tip = self.home / 'tip'
        self.tip.write_text(self.sha)
        self.env = patch.dict(os.environ, HOME=str(self.home), HERMES_HOME=str(self.home / '.hermes'),
                              PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                              POLL_LOG=str(self.log), POLL_STATE=str(self.state),
                              POLL_TIP=str(self.tip), POLL_ROOT=str(self.root))
        self.env.start()
        self.addCleanup(self.env.stop)
        self.executable('gh', '''import json,os,sys
from pathlib import Path
with open(os.environ['POLL_LOG'],'a') as f: f.write('gh\\n')
s=Path(os.environ['POLL_STATE']).read_text()
if s=='quota':
 print('GitHub quota guard: --bucket github:fixture:core',file=sys.stderr)
 sys.exit(75)
if s=='generic75': sys.exit(75)
if s=='unknown': sys.exit(1)
sha=sys.argv[sys.argv.index('--commit')+1]
print(json.dumps([dict(headSha=sha,status='in_progress' if s=='pending' else 'completed',conclusion='success' if s=='green' else 'failure',workflowName='CI',createdAt='2026-01-01')]))
''')
        self.executable('git', '''import os,sys
from pathlib import Path
args=sys.argv
root=Path(os.environ['POLL_ROOT'])
if '--show-toplevel' in args: print(root)
elif '--absolute-git-dir' in args or '--git-common-dir' in args: print(root/'.git')
elif 'get-url' in args: print('https://github.com/example/mods.git')
elif 'rev-parse' in args: print(Path(os.environ['POLL_TIP']).read_text())
''')
        self.path = 'skills/sample/SKILL.md'
        target = self.root / self.path
        target.parent.mkdir(parents=True)
        target.write_text('edit\n')
        self.pending = self.home / 'pending'
        self.pending.mkdir()

    def executable(self, name, body):
        p = self.bin / name
        p.write_text('#!' + sys.executable + '\n' + body)
        p.chmod(0o700)

    def calls(self):
        return len(self.log.read_text().splitlines()) if self.log.exists() else 0

    def records(self, count=4, deadline=None):
        for i in range(count):
            (self.pending / ('%s.json' % i)).write_text(json.dumps(dict(root=str(self.root), paths=[self.path],
                session_id='s', task_id='c', deadline=deadline or time.time()+30, signatures={})))

    def test_imported_retry_coalesces_records_and_restarts(self):
        for state in ('pending', 'red', 'unknown'):
            with self.subTest(state=state):
                self.state.write_text(state)
                self.tip.write_text({'pending':'a','red':'b','unknown':'c'}[state]*40)
                self.records()
                before = self.calls()
                for _ in range(3):
                    result=pub.retry_pending(self.pending, passes=1, interval=0)
                    self.assertEqual(result['kept'],4)
                self.assertEqual(self.calls()-before,1)
                self.assertEqual(len(list(self.pending.glob('*.json'))),4)

    def test_real_cli_restart_bounds_requests_and_expiry(self):
        for _ in range(2):
            self.records(deadline=time.time()+0.9)
            p=subprocess.run([sys.executable,str(HERE/'skill_publish.py'),'--retry-pending','--dir',str(self.pending)],
                env=dict(os.environ,HERMES_SKILL_PUBLISH_RETRY_INTERVAL='0.05'),capture_output=True,timeout=10)
            self.assertEqual(p.returncode,0,p.stderr.decode())
            self.assertEqual(list(self.pending.glob('*.json')),[])
        self.assertEqual(self.calls(),1)

    def test_changed_head_invalidates_and_green_is_never_cached(self):
        poll=getattr(pub,'polled_main_ci',pub.latest_main_ci)
        self.assertEqual(poll(self.root,self.sha),'pending')
        self.state.write_text('green')
        self.assertEqual(poll(self.root,'b'*40),'green')
        self.assertEqual(poll(self.root,'b'*40),'green')
        self.assertEqual(self.calls(),3)

    def test_concurrent_sibling_pollers_share_one_attempt(self):
        code="import importlib.util,sys; from pathlib import Path; s=importlib.util.spec_from_file_location('p',sys.argv[1]); p=importlib.util.module_from_spec(s); s.loader.exec_module(p); print(getattr(p,'polled_main_ci',p.latest_main_ci)(Path(sys.argv[2]),'a'*40))"
        children=[subprocess.Popen([sys.executable,'-c',code,str(HERE/'skill_publish.py'),str(self.root)],stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(4)]
        for child in children:
            out,err=child.communicate(timeout=10)
            self.assertEqual(child.returncode,0,err.decode())
            self.assertEqual(out.strip(),b'pending')
        self.assertEqual(self.calls(),1)

    def test_quota_refusal_holds_new_heads_until_local_recovery(self):
        self.state.write_text('quota')
        poll=getattr(pub,'polled_main_ci',pub.latest_main_ci)
        self.assertNotEqual(poll(self.root,self.sha),'green')
        self.state.write_text('green')
        self.assertNotEqual(poll(self.root,'b'*40),'green')
        self.assertEqual(self.calls(),1)

    def test_quota_ledger_recovery_without_another_gh_probe(self):
        helper=self.home/'.hermes/bin/github_quota.py'
        helper.parent.mkdir(parents=True)
        helper.write_text("from pathlib import Path\ndef reserve_ok(bucket):\n return 0 if (Path.home()/'recovered').exists() else 1\n")
        now=[1000.0]
        with patch.object(pub.time,'time',side_effect=lambda:now[0]):
            self.state.write_text('quota')
            self.assertEqual(pub.polled_main_ci(self.root,self.sha),'unknown')
            now[0]+=301
            self.state.write_text('green')
            self.assertEqual(pub.polled_main_ci(self.root,'b'*40),'unknown')
            self.assertEqual(self.calls(),1)
            (self.home/'recovered').touch()
            self.assertEqual(pub.polled_main_ci(self.root,'b'*40),'green')
            self.assertEqual(self.calls(),2)

    def test_expired_records_do_not_call_gh(self):
        self.records(deadline=time.time()-1)
        result=pub.retry_pending(self.pending,passes=1)
        self.assertEqual(result['expired'],4)
        self.assertEqual(self.calls(),0)

    def test_malformed_poll_state_never_allows_publication(self):
        self.assertEqual(pub.polled_main_ci(self.root,self.sha),'pending')
        state=self.root/'.git/skill-ci-poll/state.json'
        state.write_text('{broken')
        with self.assertRaises(ValueError):
            pub.polled_main_ci(self.root,self.sha)
        self.assertEqual(self.calls(),1)

    def test_green_overlap_holds_back_off_without_caching_an_allow(self):
        self.state.write_text('green')
        self.records()
        with patch.object(pub,'require_overlap_clear',return_value={'status':'skipped','reason':'open PR overlap overlap'}):
            for _ in range(3):
                self.assertEqual(pub.retry_pending(self.pending,passes=1)['kept'],4)
        self.assertEqual(self.calls(),4)
        self.assertEqual(len(list(self.pending.glob('*.json'))),4)

    def test_generic75_is_backed_off_but_not_invented_quota_exhaustion(self):
        self.state.write_text('generic75')
        for _ in range(3):
            self.assertEqual(pub.polled_main_ci(self.root,self.sha),'unknown')
        self.assertEqual(self.calls(),1)
        self.assertFalse((pub.quota_directory()/'hold.json').exists())

    def test_known_refusal_window_reset_permits_fresh_guarded_child(self):
        hold=pub.quota_directory()/'hold.json'
        hold.parent.mkdir(parents=True, mode=0o700)
        pub._save_private_json(hold,{'bucket':'github:fixture:core','reset':time.time()-1,'retry_at':time.time()+200})
        self.state.write_text('green')
        self.assertEqual(pub.polled_main_ci(self.root,self.sha),'green')
        self.assertEqual(self.calls(),1)

    def test_expired_operation_budget_does_not_cache_unknown_ci(self):
        with pub.operation_budget(0,time.monotonic):
            with self.assertRaises(pub.Deferred):
                pub.latest_main_ci(self.root,self.sha)
        self.assertEqual(self.calls(),0)

    def test_backoff_recovers_without_caching_green(self):
        poll=getattr(pub,'polled_main_ci',pub.latest_main_ci)
        now=[1000.0]
        with patch.object(pub.time,'time',side_effect=lambda:now[0]):
            self.assertEqual(poll(self.root,self.sha),'pending')
            now[0]+=61
            self.assertEqual(poll(self.root,self.sha),'pending')
            now[0]+=61
            self.state.write_text('green')
            self.assertEqual(poll(self.root,self.sha),'pending')
            now[0]+=61
            self.assertEqual(poll(self.root,self.sha),'green')
            self.assertEqual(poll(self.root,self.sha),'green')
        self.assertEqual(self.calls(),4)
