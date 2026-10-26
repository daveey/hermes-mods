"""Astra regressions at actual listener and socket/thread seams."""
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from types import SimpleNamespace
import urllib.request
import urllib.error

from test_hermes_chrome_agent import hca, FakeChrome
from chrome_agent_lifecycle import Ledger, Lifecycle, ScopedCDP


class TrackedChrome(FakeChrome):
    def __init__(self):
        self.clients = []
        super().__init__()

    def _conn(self, client):
        self.clients.append(client)
        try:
            super()._conn(client)
        finally:
            client.close()

    def close(self):
        for client in self.clients:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            client.close()
        try:
            self.srv.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        super().close()


class ListenerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.chrome = TrackedChrome()
        self.addCleanup(self.chrome.close)
        self.ledger = Ledger(Path(tmp.name) / 'ledger.json')
        self.wakes = []
        self.lifecycle = Lifecycle(self.ledger, 'test', lambda: self.wakes.append(True))
        self.proxy = hca.CDPProxy(0, self.chrome.port, lifecycle=self.lifecycle,
                                  show=lambda: False, logf=lambda _: None).start()
        self.addCleanup(self.proxy.close)
        self.base = 'http://127.0.0.1:%d' % self.proxy.port

    def test_raw_http_refused_running_and_idle_without_upstream_or_wake(self):
        for running in (False, True):
            self.lifecycle.running = running
            for path, method in [('/json/version', 'GET'), ('/json/list', 'GET'),
                    ('/json/new?about:blank', 'PUT'), ('/json/close/UNOWNED', 'GET'),
                    ('/json/activate/UNOWNED', 'GET'), ('/unknown', 'GET')]:
                with self.subTest(running=running, path=path):
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(urllib.request.Request(self.base + path, method=method), timeout=3)
                    self.assertEqual(error.exception.code, 403)
        self.assertEqual(self.chrome.http, [])
        self.assertEqual(self.chrome.received, [])
        self.assertEqual(self.wakes, [])

    def test_raw_browser_page_and_arbitrary_upgrades_refused(self):
        for path in ['/devtools/browser/B', '/devtools/page/UNOWNED', '/other']:
            with self.subTest(path=path):
                with socket.create_connection(('127.0.0.1', self.proxy.port), timeout=3) as client:
                    client.sendall(('GET %s HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n'
                        'Connection: Upgrade\r\nSec-WebSocket-Key: dGVzdA==\r\n\r\n' % path).encode())
                    self.assertIn(b' 403 ', client.recv(4096))
        self.assertEqual(self.chrome.handshakes, [])
        self.assertEqual(self.wakes, [])

    def test_scoped_create_and_unowned_close_through_listener(self):
        body = json.dumps(dict(installation_id='test', owner=['profile', 'task', 'session'], timeout_s=10)).encode()
        with urllib.request.urlopen(self.base + '/lifecycle/acquire', data=body, timeout=3) as response:
            grant = json.load(response)
        with urllib.request.urlopen(grant['endpoint'] + '/json/version', timeout=3) as response:
            version = json.load(response)
        client = hca.CDP(version['webSocketDebuggerUrl'])
        self.addCleanup(client.close)
        tid = client.call('Target.createTarget', {'url': 'about:blank'})['targetId']
        self.assertTrue(self.ledger.owns(grant['capability'], tid))
        with self.assertRaises(hca.CDPError):
            client.call('Target.closeTarget', {'targetId': 'UNOWNED'})
        self.assertFalse(any(m['method'] == 'Target.closeTarget' for m in self.chrome.received))


class SocketTests(unittest.TestCase):
    def test_repeated_scope_close_reclaims_reader_while_sibling_lives(self):
        with tempfile.TemporaryDirectory() as tmp:
            chrome = TrackedChrome()
            self.addCleanup(chrome.close)
            ledger = Ledger(Path(tmp) / 'ledger.json')
            life = Lifecycle(ledger, 'test', lambda: None)
            cap = ledger.acquire(['profile', 'sibling', 'session'], 30)['capability']
            proxy = SimpleNamespace(upstream=chrome.port, show=lambda: False)
            sibling = ScopedCDP(life, proxy, cap, hca)
            self.addCleanup(sibling.close)
            for index in range(5):
                grant = ledger.acquire(['profile', 'task', str(index)], 30)
                scope = ScopedCDP(life, proxy, grant['capability'], hca)
                scope.cdp.call('Browser.getVersion')
                ledger.complete(grant['capability'], grant['request_id'])
                life.control('release', dict(installation_id='test', capability=grant['capability']), '')
                scope.cdp.reader.join(timeout=2)
                self.assertFalse(scope.cdp.reader.is_alive())
                self.assertEqual(scope.cdp.sock.fileno(), -1)
                self.assertNotIn(scope, life.connections)
                sibling.cdp.call('Browser.getVersion')

    def test_close_interrupts_pending_call_and_self_close_is_safe(self):
        # A real peer that accepts websocket messages but never replies.
        srv = socket.socket()
        srv.bind(('127.0.0.1', 0))
        srv.listen()
        self.addCleanup(srv.close)
        received = threading.Event()
        peer_done = threading.Event()
        peers = []
        def peer():
            client, _ = srv.accept()
            peers.append(client)
            try:
                reader = hca._Reader(client)
                reader.read_head()
                client.sendall(b'HTTP/1.1 101 Switching Protocols\r\n\r\n')
                hca.read_frame(reader)
                received.set()
                while client.recv(4096):
                    pass
            finally:
                client.close()
                peer_done.set()
        thread = threading.Thread(target=peer, daemon=True)
        thread.start()
        client = hca.CDP('ws://127.0.0.1:%d/devtools/browser/test' % srv.getsockname()[1])
        errors = []
        def call():
            try:
                client.call('Browser.getVersion', timeout=30)
            except (hca.CDPError, OSError) as exc:
                errors.append(str(exc))
        caller = threading.Thread(target=call, daemon=True)
        caller.start()
        try:
            self.assertTrue(received.wait(3))
            client.close()
            caller.join(2)
            self.assertFalse(caller.is_alive())
            self.assertTrue(errors)
            self.assertEqual(client.pending, {})
            self.assertFalse(client.reader.is_alive())
            self.assertTrue(peer_done.wait(2))
        finally:
            for peer_socket in peers:
                try:
                    peer_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            client.close()
            caller.join(2)
            thread.join(2)

    def test_reader_callback_can_close_itself(self):
        chrome = TrackedChrome()
        self.addCleanup(chrome.close)
        client = hca.CDP('ws://127.0.0.1:%d/devtools/browser/B' % chrome.port)
        self.addCleanup(client.close)
        done = threading.Event()
        def callback(_):
            client.close()
            done.set()
        client.on_event = callback
        client.call('Browser.getVersion')
        chrome.clients[0].sendall(hca.encode_frame(1, json.dumps({'method': 'Test.event'}).encode(), mask=False))
        self.assertTrue(done.wait(3))
        client.reader.join(2)
        self.assertFalse(client.reader.is_alive())
        self.assertEqual(client.sock.fileno(), -1)
