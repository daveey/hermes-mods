"""The core-lease smoke holds a shared host test slot, so it counts against tests.host_slots."""
import os
from pathlib import Path
import tempfile
import unittest

import host_slot

SLOTS = '''
import fcntl, os
def acquire_host_slot(on_wait=None):
    d = os.environ["SLOT_DIR"]; os.makedirs(d, exist_ok=True)
    fd = os.open(os.path.join(d, "0.lock"), os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        if on_wait: on_wait()
        raise SystemExit("waited")
    return fd
'''


class HostSlotTests(unittest.TestCase):
    def core(self, source):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        scripts = Path(tmp.name) / 'scripts'
        scripts.mkdir()
        if source is not None:
            (scripts / 'run_tests_host_slots.py').write_text(source)
        return tmp.name

    def test_no_slot_module_means_uncapped(self):
        self.assertIsNone(host_slot.acquire(self.core(None)))

    def test_holds_a_slot_until_released(self):
        slots = tempfile.TemporaryDirectory()
        self.addCleanup(slots.cleanup)
        os.environ['SLOT_DIR'] = slots.name
        self.addCleanup(os.environ.pop, 'SLOT_DIR')
        core = self.core(SLOTS)
        fd = host_slot.acquire(core)
        self.assertIsNotNone(fd)
        waited = []
        with self.assertRaises(SystemExit):  # the only slot is held: a second smoke must wait
            host_slot.acquire(core, lambda: waited.append(1))
        self.assertEqual(waited, [1])
        host_slot.release(fd)
        again = host_slot.acquire(core)
        self.assertIsNotNone(again)
        host_slot.release(again)

    def test_smoke_main_takes_the_slot(self):
        source = Path(__file__).with_name('smoke_core_tools_managed.py').read_text()
        self.assertIn('host_slot.acquire(core', source)
        self.assertIn('host_slot.release(slot)', source)


if __name__ == '__main__':
    unittest.main()
