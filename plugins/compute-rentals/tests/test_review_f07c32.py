"""Regression tests for source-review wave f07c32, finding B-CR1.

B-CR1 part 1: no ``*.py`` outside the reviewed plugin bytes may run unless pinned by digest.
B-CR1 part 2: the credentialed worker subprocess is recorded in core's spawn ledger and starts isolated.
"""
import hashlib
import os
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from providers import build_collectors, load_extra_providers  # noqa: E402
from providers.config import ENV_PATH, load_config  # noqa: E402

PROVIDER_SRC = '''
from pathlib import Path
Path(__file__).with_suffix('.ran').write_text('executed')
from .common import result

PROVIDER = 'sneaky'

def build_collector():
    return lambda: result(PROVIDER, [], 'x', [])
'''


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('HERMES_HOME', str(home / '.hermes'))
    monkeypatch.setenv(ENV_PATH, str(tmp_path / 'absent.yaml'))
    return tmp_path


def test_unpinned_extras_dir_python_is_never_executed(isolated):
    extras = isolated / 'extras'
    extras.mkdir()
    (extras / 'sneaky.py').write_text(PROVIDER_SRC)
    cfg = load_config()
    cfg['extra_providers_dir'] = str(extras)
    collectors = build_collectors(cfg)
    assert 'sneaky' not in collectors
    assert not (extras / 'sneaky.ran').exists()


def test_default_extras_dir_python_is_never_executed(isolated):
    default = Path(load_config()['extra_providers_dir'])
    default.mkdir(parents=True)
    (default / 'sneaky.py').write_text(PROVIDER_SRC)
    build_collectors(load_config())
    assert not (default / 'sneaky.ran').exists()


def test_pinned_extra_loads_and_runs_only_the_pinned_bytes(isolated):
    extras = isolated / 'extras'
    extras.mkdir()
    (extras / 'sneaky.py').write_text(PROVIDER_SRC)
    collectors, _ = load_extra_providers(extras, pins={'sneaky.py': digest(extras / 'sneaky.py')})
    assert collectors['sneaky']()['status'] == 'ok'
    assert (extras / 'sneaky.ran').exists()


def test_digest_mismatch_is_refused_before_execution(isolated):
    extras = isolated / 'extras'
    extras.mkdir()
    (extras / 'sneaky.py').write_text(PROVIDER_SRC)
    pin = digest(extras / 'sneaky.py')
    (extras / 'sneaky.py').write_text(PROVIDER_SRC + '\n# edited after review\n')
    collectors, _ = load_extra_providers(extras, pins={'sneaky.py': pin})
    assert not (extras / 'sneaky.ran').exists()
    assert collectors['sneaky']()['status'] == 'error'


def test_symlinked_pinned_file_is_refused(isolated):
    outside = isolated / 'outside'
    outside.mkdir()
    (outside / 'sneaky.py').write_text(PROVIDER_SRC)
    extras = isolated / 'extras'
    extras.mkdir()
    (extras / 'sneaky.py').symlink_to(outside / 'sneaky.py')
    collectors, _ = load_extra_providers(extras, pins={'sneaky.py': digest(outside / 'sneaky.py')})
    assert not (outside / 'sneaky.ran').exists()
    assert collectors['sneaky']()['status'] == 'error'


@pytest.mark.parametrize('name', ['../sneaky.py', 'sub/sneaky.py', '_sneaky.py', 'test_sneaky.py', 'sneaky.txt'])
def test_pinned_names_must_be_plain_loadable_filenames(isolated, name):
    extras = isolated / 'extras'
    extras.mkdir()
    for target in (extras / 'sneaky.py', isolated / 'sneaky.py', extras / '_sneaky.py', extras / 'test_sneaky.py'):
        target.write_text(PROVIDER_SRC)
    collectors, _ = load_extra_providers(extras, pins={name: digest(extras / 'sneaky.py')})
    assert all(collect()['status'] == 'error' for collect in collectors.values())
    assert not list(isolated.rglob('*.ran'))


def test_executed_bytes_are_the_verified_bytes(isolated, monkeypatch):
    """The file is read once; a swap after the digest check cannot reach exec."""
    import providers
    extras = isolated / 'extras'
    extras.mkdir()
    path = extras / 'sneaky.py'
    path.write_text(PROVIDER_SRC)
    pin = digest(path)
    real = providers._read_pinned

    def swap_after_check(p, d):
        data = real(p, d)
        Path(p).write_text('raise SystemExit("swapped")')
        return data
    monkeypatch.setattr(providers, '_read_pinned', swap_after_check)
    collectors, _ = load_extra_providers(extras, pins={'sneaky.py': pin})
    assert collectors['sneaky']()['status'] == 'ok'


@pytest.mark.parametrize('pins', ['x.py', {'x.py': 'ABC'}, {'x.py': 'a' * 63}, {'': 'a' * 64}, {'x.py': 5}])
def test_config_rejects_malformed_pins(tmp_path, pins):
    import json
    path = tmp_path / 'cr.json'
    path.write_text(json.dumps({'extra_providers': pins}))
    with pytest.raises(ValueError, match='extra_providers'):
        load_config(path)


def test_config_accepts_pins(tmp_path):
    import json
    path = tmp_path / 'cr.json'
    path.write_text(json.dumps({'extra_providers': {'x.py': 'a' * 64}}))
    assert load_config(path)['extra_providers'] == {'x.py': 'a' * 64}


def _fake_ledger(monkeypatch):
    registered = []
    pkg = types.ModuleType('hermes_cli')
    pkg.__path__ = []
    mod = types.ModuleType('hermes_cli.process_identity')

    def register_child(pid, purpose, **kw):
        registered.append((pid, purpose))
        return True
    mod.register_child = register_child
    monkeypatch.setitem(sys.modules, 'hermes_cli', pkg)
    monkeypatch.setitem(sys.modules, 'hermes_cli.process_identity', mod)
    return registered


def _worker_collector(tmp_path, monkeypatch):
    import json
    from providers.runpod import RunPodCollector
    cfg = tmp_path / 'cr.json'
    cfg.write_text(json.dumps({'broker': {'python': sys.executable}}))
    monkeypatch.setenv(ENV_PATH, str(cfg))
    monkeypatch.delenv('RUNPOD_API_KEY', raising=False)
    return RunPodCollector()


def test_worker_subprocess_is_recorded_in_the_spawn_ledger(tmp_path, monkeypatch):
    registered = _fake_ledger(monkeypatch)
    import subprocess
    pids = []
    real = subprocess.Popen

    class Spy(real):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            pids.append(self.pid)
    monkeypatch.setattr(subprocess, 'Popen', Spy)
    out = _worker_collector(tmp_path, monkeypatch).collect()
    assert out['provider'] == 'runpod' and out['machines'] == []
    assert len(pids) == 1 and registered == [(pids[0], 'compute-rentals-worker')]


def test_missing_ledger_does_not_break_the_worker(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'hermes_cli.process_identity', None)  # import raises ImportError
    out = _worker_collector(tmp_path, monkeypatch).collect()
    assert out['provider'] == 'runpod' and not out.get('errors')


def test_worker_does_not_import_from_inherited_pythonpath(tmp_path, monkeypatch):
    evil = tmp_path / 'evil'
    evil.mkdir()
    marker = tmp_path / 'sitecustomize-ran'
    (evil / 'sitecustomize.py').write_text('open(%r, "w").write("x")' % str(marker))
    monkeypatch.setenv('PYTHONPATH', str(evil))
    out = _worker_collector(tmp_path, monkeypatch).collect()
    assert out['provider'] == 'runpod' and not out.get('errors')
    assert not marker.exists()


def test_worker_ignores_a_hostile_working_directory(tmp_path, monkeypatch):
    hostile = tmp_path / 'cwd'
    (hostile / 'providers').mkdir(parents=True)
    marker = tmp_path / 'shadow-ran'
    (hostile / 'providers' / '__init__.py').write_text('open(%r, "w").write("x")' % str(marker))
    monkeypatch.chdir(hostile)
    out = _worker_collector(tmp_path, monkeypatch).collect()
    assert out['provider'] == 'runpod' and not out.get('errors')
    assert not marker.exists()


def test_timed_out_worker_is_killed(tmp_path, monkeypatch):
    import subprocess
    import providers.runpod as rp
    started = []
    real = subprocess.Popen

    class Slow(rp.BrokerCollector):
        provider = 'runpod'

        def worker_argv(self):
            return [sys.executable, '-c', 'import time; time.sleep(60)']

    class Spy(real):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            started.append(self)

        def communicate(self, timeout=None):
            return super().communicate(timeout=0.2 if timeout == 70 else timeout)
    monkeypatch.setattr(subprocess, 'Popen', Spy)
    out = Slow().broker_collect()
    assert out['status'] == 'error'
    assert started[0].poll() is not None
