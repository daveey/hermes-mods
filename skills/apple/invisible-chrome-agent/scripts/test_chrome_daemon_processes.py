"""Real Unix IPC/process retirement tests; no browser required."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import unittest

from chrome_agent_lifecycle import Ledger, Lifecycle, Config
from chrome_agent_process import fingerprint
from chrome_agent_daemons import exchange, inspect, shutdown
from test_hermes_chrome_agent import hca
from chrome_test_fixtures import socket_directory


def peer(path, target, ignore):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(path)
        os.chmod(path, 0o600)
        listener.listen()
        while True:
            with listener.accept()[0] as client:
                with client.makefile('rb') as stream:
                    raw = stream.readline()
                if not raw:
                    continue  # A connected client may reject a replaced peer before sending.
                payload = json.loads(raw)
                command = payload['meta']
                if command == 'ping':
                    reply = dict(pong=True, pid=os.getpid(), browser_kind='cdp')
                elif command == 'current_tab':
                    reply = dict(targetId=target)
                elif command == 'allow_shutdown':
                    ignore = False
                    reply = dict(ok=True)
                else:
                    reply = dict(ok=True)
                client.sendall(json.dumps(reply).encode() + b'\n')
                if command == 'shutdown' and not ignore:
                    return


class RealDaemonTests(unittest.TestCase):
    def setUp(self):
        tmp = socket_directory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.now = 1000
        self.ledger = Ledger(self.root / 'ledger.json', Config(orphan_s=1), clock=lambda: self.now)

    def daemon(self, owner, target, ignore=False):
        grant = self.ledger.acquire(['profile', owner, 'default'], 10, daemon_dir=str(self.root))
        cap = grant['capability']
        self.ledger.bind(cap, owner)
        path = str(self.root / ('bu-lease-' + cap + '.sock'))
        process = subprocess.Popen([sys.executable, __file__, '--peer', path, target, str(int(ignore))])
        def cleanup():
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
            self.assertIsNone(fingerprint(process.pid))
        self.addCleanup(cleanup)
        deadline = time.monotonic() + 5
        while not Path(path).exists():
            self.assertIsNone(process.poll())
            self.assertLess(time.monotonic(), deadline)
            threading.Event().wait(.02)
        return grant, path, process

    def test_normal_sweep_preserves_active_sibling_and_foreign_page(self):
        owned, _, owned_proc = self.daemon('owned', 'owned')
        sibling, sibling_path, sibling_proc = self.daemon('sibling', 'sibling')
        foreign, foreign_path, foreign_proc = self.daemon('foreign', 'UNKNOWN')
        for grant in (owned, foreign):
            self.ledger.complete(grant['capability'], grant['request_id'])
            self.ledger.release(grant['capability'])
        self.now += 2
        self.ledger.sweep_daemons()
        owned_proc.wait(timeout=3)
        self.assertIsNone(fingerprint(owned_proc.pid))
        self.assertTrue(exchange(sibling_path, {'meta': 'ping'})['pong'])
        self.assertTrue(exchange(foreign_path, {'meta': 'ping'})['pong'])
        self.assertIsNone(sibling_proc.poll())
        self.assertIsNone(foreign_proc.poll())
        life = Lifecycle(self.ledger, 'test', lambda: None)
        self.ledger.complete(sibling['capability'], sibling['request_id'])
        self.assertTrue(life.drain(timeout=0))
        remaining = life.retire_daemons(timeout=2)
        sibling_proc.wait(timeout=3)
        self.assertEqual(remaining, 1)
        self.assertTrue(exchange(foreign_path, {'meta': 'ping'})['pong'])

    def test_shutdown_ack_without_exit_is_failure_and_keeps_retry_record(self):
        grant, path, process = self.daemon('stubborn', 'stubborn', ignore=True)
        identity = inspect(path, grant['capability'], {'stubborn'})
        self.assertIsNotNone(identity)
        start = time.monotonic()
        self.assertFalse(shutdown(identity, deadline=start + .3))
        self.assertLess(time.monotonic() - start, 2)
        life = Lifecycle(self.ledger, 'test', lambda: None)
        self.assertEqual(life.retire_daemons(timeout=.3), 1)
        self.assertIsNone(process.poll())
        with self.ledger.transaction(readonly=True) as data:
            self.assertEqual(data['leases'][grant['capability']]['daemon_socket'], path)

    def test_unresponsive_socket_respects_total_budget_and_is_untouched(self):
        grant = self.ledger.acquire(['profile', 'unknown', 'default'], 10, daemon_dir=str(self.root))
        path = self.root / ('bu-lease-' + grant['capability'] + '.sock')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(path))
            path.chmod(0o600)
            listener.listen()
            life = Lifecycle(self.ledger, 'test', lambda: None)
            start = time.monotonic()
            self.assertEqual(life.retire_daemons(timeout=.3), 1)
            self.assertLess(time.monotonic() - start, 2)
            self.assertTrue(path.exists())

    def test_idle_loop_retries_real_daemon_after_failed_shutdown(self):
        grant, path, process = self.daemon('retry', 'retry', ignore=True)
        self.ledger.complete(grant['capability'], grant['request_id'])
        self.ledger.release(grant['capability'])
        self.now += 2
        supervisor = hca.Supervisor()
        supervisor.lifecycle = Lifecycle(self.ledger, 'test', lambda: self.fail('idle retry woke browser'))
        supervisor.managed_step()
        supervisor.lifecycle.daemon_sweep.join(7)
        self.assertIsNone(process.poll())
        with self.ledger.transaction(readonly=True) as data:
            self.assertIn('daemon_socket', data['leases'][grant['capability']])
        exchange(path, {'meta': 'allow_shutdown'})
        supervisor.managed_step()
        supervisor.lifecycle.daemon_sweep.join(7)
        process.wait(timeout=3)
        self.assertIsNone(fingerprint(process.pid))
        self.assertFalse(supervisor.lifecycle.running)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--peer':
        peer(sys.argv[2], sys.argv[3], bool(int(sys.argv[4])))
    else:
        unittest.main()
