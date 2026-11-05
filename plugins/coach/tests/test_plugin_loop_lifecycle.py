"""A sibling plugin's waker loop started by a test (or at collection) must stop before its home goes away.

The goal acceptance fixtures load real chat-closeout through the plugin manager. Its register() starts an
infinite daemon ``Thread(target=_loop, args=(stop,))`` that ``manager.unload()`` never stops. Every tick
resolves HERMES_HOME afresh, so once the test's monkeypatch restored the collection home it wrote
cache/chat-closeout/ledger.db and origin-work.db there while TemporaryDirectory's atexit finalizer was
rmtree()ing it: OSError 39 printed after a green summary, exit status still 0, residue left behind.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(not Path('/proc').is_dir(), reason='needs /proc')

_WAKER = '''
import os, threading
from pathlib import Path
RECEIPT = Path(__file__).resolve().parents[2] / 'receipt'

def _loop(stop):
    # Same shape as chat-closeout's waker: wait on the stop event, act on the current home each tick.
    while not stop.wait(0.02):
        home = os.environ['HERMES_HOME']
        with open(RECEIPT, 'a') as fh:
            fh.write('tick ' + home + '\\n')
        try:
            (Path(home) / 'origin-work.db').write_text('snapshot')
        except OSError:
            pass
    with open(RECEIPT, 'a') as fh:
        fh.write('stopped ' + os.environ['HERMES_HOME'] + '\\n')

def start():
    stop = threading.Event()
    threading.Thread(target=_loop, args=(stop,), daemon=True, name='waker').start()
'''


def _suite(tmp_path):
    # conftest's _ROOT is parents[3] of its file: lay out a repo with a sibling plugin next to coach.
    root = tmp_path / 'root'
    tests = root / 'plugins' / 'coach' / 'tests'
    tests.mkdir(parents=True)
    for name in ('conftest.py', 'gateway_reaper.py', 'home_guard.py'):
        shutil.copyfile(Path(__file__).with_name(name), tests / name)
    (root / 'plugins' / 'waker').mkdir()
    (root / 'plugins' / 'waker' / '__init__.py').write_text(_WAKER)
    return root, tests


def _run(root, tests, *args):
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    # The inner collection home lives under this test's tmp_path, so a red run's residue stays contained.
    (root / 'tmp').mkdir(exist_ok=True)
    env['TMPDIR'] = str(root / 'tmp')
    env['PYTHONPATH'] = os.pathsep.join([str(root / 'plugins'), str(tests), env.get('PYTHONPATH', '')])
    return subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', *args, str(tests)],
                          env=env, cwd=tests, capture_output=True, text=True, timeout=180)


def _clean_exit(result):
    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert 'Traceback' not in out and 'Directory not empty' not in out, out


def test_plugin_loop_started_by_a_test_stops_before_its_home_is_restored(tmp_path):
    root, tests = _suite(tmp_path)
    (tests / 'test_probe.py').write_text(f'''
import os, time
import waker

def test_01_start(tmp_path):
    open({str(root / 'home01')!r}, 'w').write(str(tmp_path))
    waker.start()
    time.sleep(.1)

def test_02_later(tmp_path_factory):
    open({str(root / 'collection')!r}, 'w').write(str(tmp_path_factory.getbasetemp().parent))
    time.sleep(.2)
''')
    result = _run(root, tests)
    _clean_exit(result)
    home = (root / 'home01').read_text()
    lines = (root / 'receipt').read_text().splitlines()
    assert lines[-1] == 'stopped ' + home, lines[-5:]
    foreign = sorted({ln.split(' ', 1)[1] for ln in lines} - {home})
    assert not foreign, f'plugin loop outlived its test and wrote into other homes: {foreign}'
    assert not Path((root / 'collection').read_text()).exists(), 'collection home left behind'


def test_plugin_loop_started_at_collection_stops_before_collection_cleanup(tmp_path):
    root, tests = _suite(tmp_path)
    (tests / 'lifecycle_probe.py').write_text(f'''
import os
import waker

def pytest_collection_finish(session):
    open({str(root / 'collection')!r}, 'w').write(os.environ['HERMES_HOME'])
    waker.start()
''')
    (tests / 'test_probe.py').write_text('import time\n\ndef test_ok():\n    time.sleep(.1)\n')
    result = _run(root, tests, '-p', 'lifecycle_probe')
    _clean_exit(result)
    collection = (root / 'collection').read_text()
    lines = (root / 'receipt').read_text().splitlines()
    assert lines[-1] == 'stopped ' + collection, lines[-5:]
    assert not Path(collection).exists(), 'collection home left behind'
