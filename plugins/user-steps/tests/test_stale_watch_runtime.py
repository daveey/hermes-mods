"""Fresh-process CLI identity tests; no live homes or action stores."""
import json
import os
from pathlib import Path
import subprocess
import sys
import shlex

import pytest

from test_old_ask_review import core, request, store_snapshot

SCRIPT = Path(__file__).resolve().parents[1] / 'stale_watch.py'
OWNER = {'gatewayId': 'install:local-test', 'profile': 'default', 'storedSessionId': 'fixture-chat'}


def fixture_store(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    store = core.ActionStore(home / 'user-steps' / 'actions.sqlite3')
    item = store.create(OWNER, request(OWNER))
    return home, store, item


def invoke(home, action, rid, **extra):
    env = dict(os.environ, HOME=str(home), HERMES_HOME=str(home), PATH=str(home / 'bin'),
               PYTHONPATH='', HERMES_DISABLE_LAZY_INSTALLS='1')
    env.pop('USER_STEPS_RUNTIME_REEXEC', None)
    env.update(extra)
    return subprocess.run([sys.executable, '-I', '-S', str(SCRIPT), action, rid,
                           '--reason', 'fixture cleanup', '--evidence', 'isolated regression fixture'],
                          env=env, capture_output=True, text=True, timeout=30)


def install_launcher(home, identity='local-test'):
    # Only install/profile identity is a stand-in; CLI and all store/withdrawal code are real.
    runtime = home / 'runtime'
    package = runtime / 'hermes_cli'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('')
    (package / 'install_identity.py').write_text(f'def get_install_id(): return {identity!r}\n')
    (package / 'profiles.py').write_text("def current_profile_name(): return 'default'\n")
    bootstrap = (f'import sys, runpy; sys.path.insert(0, {str(runtime)!r}); '
                 "runpy.run_module('runpy', run_name='__main__', alter_sys=True)")
    cmd = [sys.executable, '-I', '-c', bootstrap]
    target = home / 'installed' / 'hermes'
    target.parent.mkdir()
    target.write_text('#!/bin/sh\n' + 'printf \'%s\\n\' ' + shlex.quote(json.dumps(cmd)) + '\n')
    target.chmod(0o755)
    launcher = home / '.local/bin/hermes'
    launcher.parent.mkdir(parents=True)
    launcher.symlink_to(target)
    return launcher


def test_bare_python_reexec_withdraws_local_request(tmp_path):
    home, store, item = fixture_store(tmp_path)
    install_launcher(home)
    out = invoke(home, '--withdraw', item['requestId'])
    assert out.returncode == 0, (out.stdout, out.stderr)
    assert json.loads(out.stdout)['withdrawn']['state'] == 'withdrawn'
    assert store.get(OWNER, item['requestId'])['state'] == 'withdrawn'


def test_truly_foreign_owner_remains_409_after_reexec(tmp_path):
    home, store, item = fixture_store(tmp_path)
    install_launcher(home, identity='another-install')
    before = store_snapshot(store)
    out = invoke(home, '--withdraw', item['requestId'])
    assert out.returncode == 2
    assert json.loads(out.stdout) == {'error': 'Request owner is not on this gateway; not withdrawn', 'status': 409}
    assert store_snapshot(store) == before


@pytest.mark.parametrize('action', ['--withdraw', '--reviewed'])
@pytest.mark.parametrize('failure', ['missing-identity', 'loop-marker', 'malformed-command', 'missing-python'])
def test_failed_runtime_recovery_is_bounded_and_never_writes(tmp_path, action, failure):
    home, store, item = fixture_store(tmp_path)
    launcher = install_launcher(home, identity='' if failure == 'missing-identity' else 'local-test')
    if failure == 'malformed-command':
        launcher.write_text("#!/bin/sh\nprintf '[]\\n'\n")
    if failure == 'missing-python':
        text = launcher.read_text().replace(sys.executable, str(home / 'missing-python'))
        launcher.write_text(text)
    before = store_snapshot(store)
    env = {'USER_STEPS_RUNTIME_REEXEC': '1'} if failure == 'loop-marker' else {}
    out = invoke(home, action, item['requestId'], **env)
    assert out.returncode == 2, (out.stdout, out.stderr)
    assert json.loads(out.stdout)['status'] == 503
    assert 'gateway identity unavailable under' in out.stdout
    assert 'not on this gateway' not in out.stdout
    assert store_snapshot(store) == before


@pytest.mark.parametrize('action', ['--dry-run'])
def test_detection_cli_without_identity_never_initializes_store(tmp_path, action):
    home = tmp_path / 'empty-home'
    home.mkdir()
    env = dict(os.environ, HOME=str(home), HERMES_HOME=str(home), PATH=str(home / 'bin'))
    out = subprocess.run([sys.executable, '-I', '-S', str(SCRIPT), action], env=env,
                         text=True, capture_output=True, timeout=30)
    assert out.returncode == 2, (out.stdout, out.stderr)
    assert json.loads(out.stdout)['status'] == 503
    assert not (home / 'user-steps').exists()


def test_withdraw_without_identity_and_launcher_is_503_without_writes(tmp_path):
    home, store, item = fixture_store(tmp_path)
    before = store_snapshot(store)
    out = invoke(home, '--withdraw', item['requestId'])
    assert out.returncode == 2, out.stderr
    result = json.loads(out.stdout)
    assert result['status'] == 503, result
    assert 'gateway identity unavailable under' in result['error']
    assert 'hermes' in result['error']
    assert 'not on this gateway' not in out.stdout and 'foreign' not in out.stdout
    assert store_snapshot(store) == before


def test_reviewed_without_identity_and_launcher_is_503_without_writes(tmp_path):
    home, store, item = fixture_store(tmp_path)
    before = store_snapshot(store)
    out = invoke(home, '--reviewed', item['requestId'])
    assert out.returncode == 2, out.stdout
    result = json.loads(out.stdout)
    assert result['status'] == 503, result
    assert 'gateway identity unavailable under' in result['error']
    assert 'not on this gateway' not in out.stdout and 'foreign' not in out.stdout
    assert store_snapshot(store) == before
