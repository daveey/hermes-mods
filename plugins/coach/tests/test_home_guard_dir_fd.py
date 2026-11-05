"""Real audit events must use the directory fd, never the caller's cwd."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.name != 'posix', reason='needs directory fds')
@pytest.mark.parametrize('case', ['cleanup', 'foreign', 'traversal', 'symlink', 'absolute', 'ordinary', 'bytes', 'thread', 'earlier_hook', 'earlier_object', 'repeat_install'])
def test_fd_relative_audit_target(tmp_path, case):
    # Each probe owns its process: audit hooks cannot be uninstalled.
    script = r'''
import os, sys, shutil, threading
from pathlib import Path
from home_guard import HomeGuard
root = Path(sys.argv[1])
case = sys.argv[2]
owned = root / 'owned'
foreign = root / 'foreign'
owned.mkdir()
foreign.mkdir()
(owned / 'child' / 'nested').mkdir(parents=True)
(foreign / 'secret').write_text('private')
(owned / 'link').symlink_to(foreign, target_is_directory=True)
fd_owned = os.open(owned, os.O_RDONLY)
fd_foreign = os.open(foreign, os.O_RDONLY)
os.chdir(foreign if case in ('cleanup', 'bytes', 'earlier_hook', 'earlier_object', 'repeat_install') else owned)
if case in ('earlier_hook', 'earlier_object'):
    (owned / 'x').write_text('owned')
    (foreign / 'x').write_text('private')
    hook_denied = []
    active = False
    def earlier(event, args):
        global active
        if event == 'open' and args[0] == 'x' and not active:
            active = True
            try:
                with open(args[0] if case == 'earlier_object' else 'x'):
                    pass
            except AssertionError:
                hook_denied.append(True)
            finally:
                active = False
    sys.addaudithook(earlier)
guard = HomeGuard(foreign, allowed=[owned])
guard.install()
if case == 'repeat_install':
    guard.install()
def denied(path, **kwargs):
    try:
        fd = os.open(path, os.O_RDONLY, **kwargs)
    except AssertionError:
        return
    os.close(fd)
    raise AssertionError('foreign access escaped guard')
if case in ('cleanup', 'repeat_install'):
    shutil.rmtree(owned / 'child')
    assert not (owned / 'child').exists()
    assert not guard.violations
elif case == 'bytes':
    fd = os.open(b'child', os.O_RDONLY, dir_fd=fd_owned)
    os.close(fd)
    assert not guard.violations
elif case == 'foreign':
    denied('secret', dir_fd=fd_foreign)
elif case == 'traversal':
    denied('../foreign/secret', dir_fd=fd_owned)
elif case == 'symlink':
    denied('link/secret', dir_fd=fd_owned)
elif case == 'absolute':
    denied(str(foreign / 'secret'), dir_fd=fd_owned)
elif case == 'ordinary':
    os.chdir(foreign)
    denied('secret')
elif case == 'thread':
    errors = []
    def probe():
        try:
            denied('secret', dir_fd=fd_foreign)
        except BaseException as exc:
            errors.append(exc)
    thread = threading.Thread(target=probe)
    thread.start()
    thread.join()
    assert not errors, errors
elif case in ('earlier_hook', 'earlier_object'):
    fd = os.open('x', os.O_RDONLY, dir_fd=fd_owned)
    os.close(fd)
    assert hook_denied == [True]
    assert len(guard.violations) == 1
    assert str(foreign / 'x') in guard.violations[0]
if case not in ('cleanup', 'bytes', 'earlier_hook', 'earlier_object', 'repeat_install'):
    assert len(guard.violations) == 1, guard.violations
    assert str(foreign / 'secret') in guard.violations[0]
os.close(fd_owned)
os.close(fd_foreign)
print('verified', case)
'''
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join([str(Path(__file__).parent), env.get('PYTHONPATH', '')])
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path), case],
                            cwd=tmp_path, env=env, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Traceback' not in result.stderr
    assert f'verified {case}' in result.stdout
