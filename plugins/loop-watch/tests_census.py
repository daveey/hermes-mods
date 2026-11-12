import importlib.util
import json
import os
import subprocess
from pathlib import Path
import sys
import threading
import types
import weakref
from collections import OrderedDict

import pytest

spec = importlib.util.spec_from_file_location('census_test', Path(__file__).with_name('census.py'))
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


class Box:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def runner(agents):
    return Box(_agent_cache=OrderedDict((str(i), (a, 'private-signature')) for i, a in enumerate(agents)),
               _agent_cache_lock=threading.Lock(), _sessions={})


def request(p, content=c.COMMAND):
    path = p.path / 'request'
    path.write_bytes(content)
    path.chmod(0o600)
    return path


def test_default_off(tmp_path):
    assert c.start(tmp_path, env={}) is None
    assert list(tmp_path.iterdir()) == []


def test_counts_sizes_and_privacy():
    message = {'content': 'TOP SECRET', 'role': 'user'}
    messages = [message]
    a = Box(_session_messages=messages, conversation_history=messages)
    b = Box(_session_messages=[])
    r = runner([a, b])
    r._sessions['0'] = Box(turn=Box(agent=a))
    result = c.scan(r)
    assert result['agents'] == 2
    assert result['active'] == result['idle'] == 1
    assert result['containers'] == 3
    assert result['shallow_container_bytes'] == sum(map(sys.getsizeof, (messages, message, b._session_messages)))
    assert result['buffer_lengths'] == {'_session_messages': 1, 'conversation_history': 1}
    assert result['retained_bytes'] is None
    assert 'TOP SECRET' not in json.dumps(result)
    assert 'private-signature' not in json.dumps(result)


def test_no_custom_protocols():
    class Trap:
        @property
        def __dict__(self):
            raise AssertionError('property')
        def __sizeof__(self):
            raise AssertionError('sizeof')
        def __repr__(self):
            raise AssertionError('repr')
    class EvilList(list):
        def __iter__(self):
            raise AssertionError('iter')
    assert c.storage(Trap()) == {}
    result = c.scan(runner([Box(_session_messages=[Trap(), EvilList()])]))
    assert result['containers'] == 1


def test_limits_and_deadline():
    assert c.scan(runner([Box() for _ in range(200)]))['agents'] == 128
    result = c.scan(runner([Box(_session_messages=[{} for _ in range(20000)])]))
    assert result['containers'] <= 10000
    assert result['visited_values'] <= 10000
    assert result['truncated']
    ticks = iter([0, .101, .102])
    result = c.scan(runner([Box()]), clock=lambda: next(ticks))
    assert result['deadline_exceeded'] and result['agents'] == 0


def test_unknown_and_busy():
    assert c.scan()['status'] == 'unknown'
    r = runner([])
    with r._agent_cache_lock:
        assert c.scan(r)['status'] == 'unknown'


def test_registry_without_import(monkeypatch):
    monkeypatch.delitem(sys.modules, 'gateway.run', raising=False)
    assert c.runner_reference() is None
    module = types.ModuleType('gateway.run')
    r = runner([])
    module.GatewayRunner = Box
    module._gateway_runner_ref = weakref.ref(r)
    monkeypatch.setitem(sys.modules, 'gateway.run', module)
    assert c.runner_reference() is r
    module._gateway_runner_ref = lambda: (_ for _ in ()).throw(AssertionError())
    assert c.runner_reference() is None


def test_one_shot_and_output(tmp_path):
    p = c.Probe(tmp_path, runner=lambda: runner([]))
    try:
        request(p)
        p.tick()
        output = (p.path / 'result.json').read_bytes()
        assert json.loads(output)['status'] == 'ok'
        assert len(output) <= c.MAX_OUTPUT
        assert (p.path / 'result.json').stat().st_mode & 0o777 == 0o600
        assert p.path.stat().st_mode & 0o777 == 0o700
        request(p)
        p.tick()
        assert (p.path / 'request').exists()
        assert (p.path / 'result.json').read_bytes() == output
    finally:
        p.close()


@pytest.mark.parametrize('bad', ['symlink', 'fifo', 'directory', 'stale', 'mode', 'command', 'hardlink'])
def test_reject_bad_request(tmp_path, bad):
    p = c.Probe(tmp_path, runner=lambda: (_ for _ in ()).throw(AssertionError('scanned')))
    try:
        path = p.path / 'request'
        if bad == 'symlink':
            target = tmp_path / 'target'
            target.write_bytes(c.COMMAND)
            path.symlink_to(target)
        elif bad == 'fifo':
            os.mkfifo(path, 0o600)
        elif bad == 'directory':
            path.mkdir()
        else:
            request(p, b'wrong' if bad == 'command' else c.COMMAND)
            if bad == 'stale':
                os.utime(path, ns=(1, 1))
            if bad == 'mode':
                path.chmod(0o644)
            if bad == 'hardlink':
                os.link(path, p.path / 'other')
        p.tick()
        assert p.used
        assert not (p.path / 'result.json').exists()
    finally:
        p.close()


def test_private_root_and_overlap(tmp_path):
    root = tmp_path / 'loop-watch-census'
    root.mkdir(mode=0o755)
    with pytest.raises(OSError):
        c.Probe(tmp_path)
    root.rmdir()
    root.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(OSError):
        c.Probe(tmp_path)
    root.unlink()
    p = c.Probe(tmp_path)
    try:
        request(p)
        with p.guard:
            p.tick()
        assert not p.used
        p.write('large.json', {'value': 'x' * c.MAX_OUTPUT})
        assert json.loads((p.path / 'large.json').read_text())['output_truncated']
    finally:
        p.close()


@pytest.mark.parametrize('enabled', [False, True])
def test_normal_plugin_discovery(tmp_path, enabled):
    pytest.importorskip('hermes_cli.plugins')
    home = tmp_path / 'home'
    plugins = home / 'plugins'
    plugins.mkdir(parents=True)
    (plugins / 'loop-watch').symlink_to(Path(__file__).resolve().parent, target_is_directory=True)
    (home / 'config.yaml').write_text('plugins:\n  enabled: [loop-watch]\n')
    code = r'''
import json, os, sys, time
from pathlib import Path
from hermes_cli.plugins import PluginManager
home = Path(os.environ['HERMES_HOME'])
sys.argv = ['hermes', 'gateway', 'run']
# Isolate the existing CPU/stall watcher; census itself runs normally.
sys._hermes_loop_watch_started = True
manager = PluginManager(str(home))
manager.discover_and_load()
loaded = manager._plugins['loop-watch']
assert loaded.enabled and not loaded.error, loaded.error
root = home / 'loop-watch-census'
if os.environ['HERMES_LOOP_WATCH_CACHE_CENSUS'] == '1':
    directories = list(root.iterdir())
    assert len(directories) == 1
    directory = directories[0]
    request = directory / 'request'
    request.write_bytes(b'cache-census-v1\n')
    request.chmod(0o600)
    end = time.monotonic() + 5
    while not (directory / 'result.json').exists() and time.monotonic() < end:
        time.sleep(.02)
    assert json.loads((directory / 'result.json').read_text())['status'] == 'unknown'
else:
    assert not root.exists()
print('normal plugin discovery passed')
'''
    env = dict(os.environ, HERMES_HOME=str(home),
               HERMES_LOOP_WATCH_CACHE_CENSUS='1' if enabled else '0')
    result = subprocess.run([sys.executable, '-c', code], env=env, cwd=tmp_path,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'normal plugin discovery passed' in result.stdout


def test_generation_targeting(tmp_path):
    a, b = c.Probe(tmp_path), c.Probe(tmp_path)
    try:
        request(a)
        b.tick()
        assert not b.used
        assert not (b.path / 'result.json').exists()
    finally:
        a.close()
        b.close()
