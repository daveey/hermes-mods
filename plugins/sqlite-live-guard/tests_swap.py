"""Real CLI fixture tests. Never point at the user's home/database/services.

Run: python3 -m unittest discover -s plugins/sqlite-live-guard -p tests_swap.py -v
Unprivileged runs scope the CLI's holder scan to this test process's own
descendants (SCOPE below): the fixture DB lives in a private temp dir, so only
processes the test starts can hold it, and other users' processes (pid 1 on a
non-root host) can't make the scan incomplete. Production holders() still scans
every pid and refuses an unreadable one (test_unscoped_scan_still_fails_closed).
SWAP_TEST_PRIVILEGED_SCAN=1 (implied on GitHub Actions) instead runs the real
whole-host scan through the helper's sudo read-only scanner.
Core-backed cases skip without core.
The holder scan is Linux-only: on a host without /proc/self/fd (macOS) the cases
that reach it skip (needs_proc); test_missing_proc_refuses asserts the CLI refusal.
"""
import contextlib
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(os.environ.get('SWAP_TEST_SCRIPT', Path(__file__).with_name('swap_db.py'))).resolve()
CORE = importlib.util.find_spec('gateway') is not None
# Hosted Linux CI permits passwordless sudo; test the real scanner instead of
# weakening production's permission-denied refusal or skipping holder coverage.
PRIVILEGED = os.environ.get('SWAP_TEST_PRIVILEGED_SCAN') == '1' or os.environ.get('GITHUB_ACTIONS') == 'true'
# Loads the CLI module as `m`. Unprivileged, it narrows m._proc_entries to the
# descendants of SWAP_TEST_SCOPE_PID (the test process); the scan logic,
# including the fail-closed refusal of an unreadable pid, runs unchanged on them.
LOAD = ('import importlib.util,os,sys\n'
        + f's=importlib.util.spec_from_file_location("swap",{str(SCRIPT)!r}); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)\n')
SCOPE = '' if PRIVILEGED else (
    'def _scoped(entries=m._proc_entries):\n'
    ' root=int(os.environ["SWAP_TEST_SCOPE_PID"]); listed=list(entries()); parent={}\n'
    ' for p in listed:\n'
    '  if p.name.isdigit():\n'
    '   try: parent[int(p.name)]=int((p/"stat").read_text().rsplit(")",1)[1].split()[1])\n'
    '   except (OSError,IndexError,ValueError): pass\n'
    ' def mine(pid):\n'
    '  seen=set()\n'
    '  while pid and pid not in seen:\n'
    '   if pid==root: return True\n'
    '   seen.add(pid); pid=parent.get(pid)\n'
    '  return False\n'
    ' return [p for p in listed if p.name.isdigit() and mine(int(p.name))]\n'
    'm._proc_entries=_scoped\n')
PREAMBLE = LOAD + SCOPE
HAS_PROC = Path('/proc/self/fd').is_dir()
# Only for cases that get as far as the holder scan; cases refused earlier run on every host.
needs_proc = unittest.skipUnless(HAS_PROC, 'needs the Linux holder scan: this host has no /proc/self/fd')


def database(path, sql='CREATE TABLE sessions(id TEXT PRIMARY KEY); INSERT INTO sessions VALUES("saved");'):
    with contextlib.closing(sqlite3.connect(path)) as con:
        con.executescript(sql)


class SwapCLI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='swap-fixture-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.db, self.replacement = self.root / 'fixture.db', self.root / 'recovered.db'
        database(self.db)
        database(self.replacement)
        self.env = dict(os.environ, HERMES_HOME=str(self.root / 'home'))
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        fake = self.bin / 'systemctl'
        fake.write_text('#!' + sys.executable + '\nimport os,sys\nwith open(os.environ["UNIT_LOG"],"a") as f: f.write(" ".join(sys.argv[1:])+"\\n")\n')
        fake.chmod(0o700)
        self.env.update(PATH=str(self.bin) + os.pathsep + os.environ['PATH'], UNIT_LOG=str(self.root / 'units.log'),
                        SWAP_TEST_SCOPE_PID=str(os.getpid()))
        self.launcher = self.root / 'launch.py'
        self.launcher.write_text(PREAMBLE + 'sys.exit(m.main())\n')

    def cli(self, *args, script=None):
        cmd = [sys.executable, str(script or self.launcher), '--db', str(self.db), '--replacement', str(self.replacement), '--wait', '0']
        if PRIVILEGED:
            cmd += ['--holder-scan-sudo']
        return subprocess.run(cmd + list(args), env=self.env, capture_output=True, text=True, timeout=90)

    def successful(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        receipt = self.receipt()
        self.assertEqual(receipt['phase'], 'complete')
        self.assertTrue(Path(receipt['archive']).exists())
        self.assertFalse(Path(str(self.db) + '.swap-pending.json').exists())
        self.assertEqual(self.db.stat().st_mode & 0o777, 0o600)
        return receipt

    def receipt(self):
        return json.loads(next(self.root.glob('.*.recovery-*/receipt.json')).read_text())

    def state(self):
        if not CORE:
            self.skipTest('Hermes core unavailable; recovered-state E2E gate remains open')
        self.db = self.db.rename(self.root / 'state.db')

    def add(self, path, sql):
        with contextlib.closing(sqlite3.connect(path)) as con:
            con.executescript(sql)

    @needs_proc
    def test_normal_install(self):
        self.successful(self.cli('--stop-unit', 'fixture.service'))
        self.assertEqual((self.root / 'units.log').read_text().splitlines(),
                         ['--user stop -- fixture.service', '--user start -- fixture.service'])

    def test_single_owner_lock(self):
        with open(str(self.db) + '.recovery.lock', 'a+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn('another recovery', result.stderr)

    @needs_proc
    def test_core_maintenance_lock(self):
        self.state()
        with open(self.db.parent / 'gateway.lock', 'a+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.cli('--recovered-state')
        self.assertEqual(result.returncode, 2)
        self.assertIn('Exclusive maintenance refused', result.stderr)

    @needs_proc
    def test_allowed_empty_runtime_omission_requires_ack(self):
        self.state()
        self.add(self.db, 'CREATE TABLE worker_receipts(id TEXT);')
        result = self.cli('--recovered-state')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('--ack-runtime-loss', result.stderr)
        result = self.cli('--recovered-state', '--ack-runtime-loss')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('worker_receipts', result.stdout)

    @needs_proc
    def test_allowed_omission_install(self):
        self.state()
        self.add(self.db, 'CREATE TABLE worker_receipts(id TEXT);')
        self.assertEqual((r := self.cli('--recovered-state', '--ack-runtime-loss')).returncode, 0,
                         r.stdout + r.stderr)

    @needs_proc
    def test_unknown_and_durable_omissions_fail(self):
        for table in ('hosted_room_unknown', 'security_revocations', 'delivery_obligations'):
            with self.subTest(table=table):
                self.add(self.db, f'CREATE TABLE {table}(id TEXT);')
                result = self.cli()
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn(table, result.stderr)
                self.add(self.db, f'DROP TABLE {table};')

    @needs_proc
    def test_recreated_empty_security_table_is_not_preservation(self):
        self.state()
        self.add(self.db, 'CREATE TABLE security_revocations(id TEXT); INSERT INTO security_revocations VALUES("revoked");')
        self.add(self.replacement, 'CREATE TABLE security_revocations(id TEXT);')
        result = self.cli('--recovered-state', '--ack-runtime-loss')
        self.assertEqual(result.returncode, 2)
        self.assertIn('row loss in security_revocations', result.stderr)

    @needs_proc
    def test_populated_runtime_idempotency_omission_refused(self):
        self.state()
        self.add(self.db, 'CREATE TABLE session_admissions(id TEXT); INSERT INTO session_admissions VALUES("already-started");')
        result = self.cli('--recovered-state', '--ack-runtime-loss')
        self.assertEqual(result.returncode, 2)
        self.assertIn('row loss in session_admissions', result.stderr)

    @needs_proc
    def test_epoch_floor_retained(self):
        self.state()
        self.add(self.db, 'CREATE TABLE runtime_epoch(singleton INTEGER PRIMARY KEY, epoch INTEGER, instance_id TEXT); INSERT INTO runtime_epoch VALUES(1,42,"old");')
        self.successful(self.cli('--recovered-state', '--ack-runtime-loss'))
        with contextlib.closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute('SELECT epoch FROM runtime_epoch').fetchone(), (42,))

    def test_fk_failure(self):
        self.add(self.replacement, 'CREATE TABLE parent(id INTEGER PRIMARY KEY); CREATE TABLE child(pid INTEGER REFERENCES parent(id)); INSERT INTO child VALUES(99);')
        result = self.cli('--stop-unit', 'fixture.service')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('foreign_key_check', result.stderr)
        self.assertFalse((self.root / 'units.log').exists())

    @needs_proc
    def test_replacement_committed_wal_included(self):
        con = sqlite3.connect(self.replacement)
        self.addCleanup(con.close)
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('PRAGMA wal_autocheckpoint=0')
        con.execute('INSERT INTO sessions VALUES("wal-only")')
        con.commit()
        self.assertTrue(Path(str(self.replacement) + '-wal').exists())
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with contextlib.closing(sqlite3.connect(self.db)) as installed:
            self.assertEqual(installed.execute('SELECT count(*) FROM sessions').fetchone(), (2,))
        self.successful(result)

    def holder(self, suffix='', deleted=False, mapped=False):
        target = Path(str(self.db) + suffix)
        if suffix:
            target.write_bytes(b'x' * 4096)
        child = self.root / 'hold.py'
        child.write_text('import os,sys,time,mmap\nf=open(sys.argv[1],"rb")\n'
                         + ('m=mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ); f.close()\n' if mapped else '')
                         + ('os.unlink(sys.argv[1])\n' if deleted else '')
                         + 'print("ready",flush=True)\ntime.sleep(90)\n')
        proc = subprocess.Popen([sys.executable, str(child), str(target), 'serve', '--isolated', '--ssh-session-token-file', 'fixture-token'],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def cleanup():
            proc.terminate()
            proc.communicate(timeout=10)
        self.addCleanup(cleanup)
        assert proc.stdout is not None
        self.assertEqual(proc.stdout.readline().strip(), 'ready')
        return proc

    @needs_proc
    def test_live_ssh_serve_holder_blocks_and_no_abort_restart(self):
        proc = self.holder()
        result = self.cli('--stop-unit', 'fixture.service')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        log = (self.root / 'units.log').read_text()
        self.assertNotIn('start', log)
        self.assertIn(str(proc.pid), result.stderr)
        self.assertIn('birth=', result.stderr)
        self.assertIn('serve', result.stderr)
        self.assertEqual((self.root / 'units.log').read_text(), '--user stop -- fixture.service\n')
        self.assertTrue(self.db.exists())

    @needs_proc
    def test_active_sidecar_holder_blocks(self):
        proc = self.holder('-wal')
        result = self.cli()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(str(proc.pid), result.stderr)
        self.assertIn('fixture.db-wal', result.stderr)

    @needs_proc
    def test_deleted_sidecar_holder_blocks(self):
        self.holder('-wal', deleted=True)
        result = self.cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn('(deleted)', result.stderr)

    @needs_proc
    def test_mmap_only_deleted_sidecar_blocks(self):
        self.holder('-shm', deleted=True, mapped=True)
        result = self.cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn('(deleted)', result.stderr)

    @needs_proc
    def test_unreadable_original_refused(self):
        self.db.write_bytes(b'not a sqlite database')
        result = self.cli()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(self.db.read_bytes(), b'not a sqlite database')

    @needs_proc
    def test_partial_rename_never_restarts_and_retry_refuses(self):
        # Only OS fault injection; execute the actual argparse CLI and all real SQLite paths.
        wrapper = self.root / 'fault.py'
        wrapper.write_text(PREAMBLE
            + 'original=m.os.rename\ndef rename(src,dst):\n'
            + ' if str(src).endswith("staged.db"): raise OSError("fixture install rename failure")\n'
            + ' return original(src,dst)\nm.os.rename=rename\nsys.exit(m.main())\n')
        result = self.cli('--stop-unit', 'fixture.service', script=wrapper)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('fixture install rename failure', result.stderr)
        self.assertEqual((self.root / 'units.log').read_text(), '--user stop -- fixture.service\n')
        receipt = self.receipt()
        self.assertEqual(receipt['failed_phase'], 'installing')
        self.assertTrue(Path(receipt['archive']).exists())
        self.assertTrue(Path(receipt['stage']).exists())
        self.assertTrue(Path(str(self.db) + '.swap-pending.json').exists())
        self.assertTrue(self.db.exists())  # canonical main never disappears during atomic publication
        retry = self.cli()
        self.assertNotEqual(retry.returncode, 0)

    def test_state_install_cannot_bypass_policy(self):
        self.state()
        result = self.cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn('requires --recovered-state', result.stderr)

    @needs_proc
    def test_autoincrement_floor_preserved(self):
        self.state()
        for path in (self.db, self.replacement):
            self.add(path, 'CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT); INSERT INTO messages VALUES(1);')
        self.add(self.db, 'UPDATE sqlite_sequence SET seq=1000 WHERE name="messages";')
        self.successful(self.cli('--recovered-state'))
        with contextlib.closing(sqlite3.connect(self.db)) as con:
            con.execute('INSERT INTO messages DEFAULT VALUES')
            self.assertEqual(con.execute('SELECT max(id) FROM messages').fetchone(), (1001,))

    @needs_proc
    def test_empty_runtime_omission_preserves_historical_sequence(self):
        self.state()
        for path in (self.db, self.replacement):
            self.add(path, 'CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT); INSERT INTO messages VALUES(1);')
        self.add(self.db, 'CREATE TABLE session_admissions(seq INTEGER PRIMARY KEY AUTOINCREMENT); '
                         'INSERT INTO session_admissions VALUES(100); DELETE FROM session_admissions;')
        self.successful(self.cli('--recovered-state', '--ack-runtime-loss'))
        with contextlib.closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute('SELECT seq FROM sqlite_sequence WHERE name="session_admissions"').fetchone(), (100,))

    @needs_proc
    def test_post_rename_original_holder_blocks(self):
        # Simulate an uncoordinated reader opening just after the last pre-rename scan.
        child = self.root / 'late_holder.py'
        child.write_text('import sys,time\nf=open(sys.argv[1],"rb")\nprint("ready",flush=True)\ntime.sleep(90)\n')
        wrapper = self.root / 'late.py'
        wrapper.write_text(PREAMBLE + 'import subprocess\n'
            + 'original=m.os.rename\nchildren=[]\ndef rename(src,dst):\n'
            + ' if str(src).endswith("staged.db"):\n'
            + f'  p=subprocess.Popen([sys.executable,{str(child)!r},str(dst)],stdout=subprocess.PIPE,text=True); children.append(p); assert p.stdout.readline().strip()=="ready"\n'
            + ' return original(src,dst)\nm.os.rename=rename\ntry: result=m.main()\nfinally:\n'
            + ' for p in children: p.terminate(); p.wait(timeout=5)\nsys.exit(result)\n')
        result = self.cli('--stop-unit', 'fixture.service', script=wrapper)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('late_holder.py', result.stderr)
        self.assertTrue(Path(str(self.db) + '.swap-pending.json').exists())
        self.assertEqual((self.root / 'units.log').read_text(), '--user stop -- fixture.service\n')

    @needs_proc
    def test_actual_sessions_recover_then_install(self):
        self.state()
        self.db.unlink()
        self.replacement.unlink()
        setup = self.root / 'seed_core.py'
        setup.write_text('from hermes_state import SessionDB\nfrom pathlib import Path\n'
                         + f'db=SessionDB(Path({str(self.db)!r}))\n'
                         + 'db.create_session("fixture-session", "cli")\ndb.close()\n')
        seed = subprocess.run([sys.executable, str(setup)], env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(seed.returncode, 0, seed.stdout + seed.stderr)
        report = self.root / 'recover.json'
        recovery = subprocess.run([sys.executable, '-m', 'hermes_cli.main', 'sessions', 'recover',
                                   '--source', str(self.db), '--output', str(self.replacement),
                                   '--report', str(report)], env=self.env, capture_output=True, text=True, timeout=120)
        self.assertEqual(recovery.returncode, 0, recovery.stdout + recovery.stderr)
        self.assertTrue(report.exists())
        self.successful(self.cli('--recovered-state', '--ack-runtime-loss'))
        with contextlib.closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute('SELECT id FROM sessions').fetchall(), [('fixture-session',)])

    @needs_proc
    def test_recheck_before_rename(self):
        wrapper = self.root / 'racer.py'
        wrapper.write_text(PREAMBLE
            + 'original=m._policy\ndef policy(*args):\n r=original(*args); m._no_holders=lambda *a: (_ for _ in ()).throw(m.Refusal("fixture late holder")); return r\n'
            + 'm._policy=policy\nsys.exit(m.main())\n')
        result = self.cli(script=wrapper)
        self.assertEqual(result.returncode, 2)
        self.assertIn('fixture late holder', result.stderr)
        self.assertTrue(self.db.exists())

    @needs_proc
    def test_unscoped_scan_still_fails_closed(self):
        # The test scope must not hide production's refusal of an unreadable pid.
        if PRIVILEGED or os.access('/proc/1/fd', os.R_OK):
            self.skipTest('every pid is readable here; no unreadable holder to refuse')
        result = self.cli(script=SCRIPT)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('incomplete holder scan', result.stderr)
        self.assertTrue(self.db.exists())


    def test_missing_proc_refuses(self):
        # Real unsupported-host CLI on macOS; fault only the capability probe on Linux.
        script = SCRIPT
        if HAS_PROC:
            script = self.root / 'noproc.py'
            script.write_text(LOAD
                + 'real=m.Path.is_dir\n'
                + 'm.Path.is_dir=lambda self: False if str(self)=="/proc/self/fd" else real(self)\n'
                + 'sys.exit(m.main())\n')
        # Keep the probe in this process, even on privileged CI (sudo starts a fresh interpreter).
        result = subprocess.run([sys.executable, str(script), '--db', str(self.db),
                                 '--replacement', str(self.replacement), '--wait', '0',
                                 '--stop-unit', 'fixture.service'],
                                env=self.env, capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('holder inspection requires Linux /proc', result.stderr)
        self.assertEqual((self.root / 'units.log').read_text(), '--user stop -- fixture.service\n')
        with contextlib.closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute('SELECT id FROM sessions').fetchall(), [('saved',)])
        self.assertFalse(list(self.root.glob('.*.recovery-*/original.db')))


if __name__ == '__main__':
    unittest.main()
