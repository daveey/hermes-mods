"""Private capability socket identity checks, including an empty/crashed browser."""
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest import mock

import chrome_agent_daemons as daemons


class DaemonIdentityTests(unittest.TestCase):
    def test_empty_scoped_daemon_is_recoverable_but_foreign_page_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bu-lease-testcap.sock'
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(path))
                path.chmod(0o600)
                ping = dict(pong=True, pid=os.getpid(), browser_kind='cdp')
                with mock.patch.object(daemons, 'exchange', side_effect=[ping, {'error': 'not_attached'}]):
                    identity = daemons.inspect(str(path), 'testcap', set())
                    self.assertIsNotNone(identity)
                    self.assertEqual(identity['pid'], os.getpid())
                with mock.patch.object(daemons, 'exchange', side_effect=[ping, {'targetId': 'foreign'}]):
                    self.assertIsNone(daemons.inspect(str(path), 'testcap', {'owned'}))
                with mock.patch.object(daemons, 'exchange') as exchange:
                    self.assertIsNone(daemons.inspect(str(path), 'another-capability', {'owned'}))
                    exchange.assert_not_called()
