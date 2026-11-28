"""Real shells, no network and no deletion: rm only records cwd/operands.

FOLLOWUP_ZSH may name a workspace-local extracted zsh when the host lacks it.
"""
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from followup_corpus import BODIES

REAL_BODIES = dict(BODIES, ssh_relative='rm -rf .hermes',
                   ssh_failed_cd_semi='cd /nonexistent; rm -rf .hermes',
                   ssh_failed_cd_or='cd /nonexistent || rm -rf .hermes')

_FAKE_RM = '''import json, os, sys
from pathlib import Path
with open(os.environ['RM_RECEIPT'], 'a') as f:
    f.write(json.dumps({'cwd': os.getcwd(), 'args': sys.argv[1:]})+'\\n')
'''
# A shell that does not implement pushd/builtin is a real negative control.
_PARENT_ONLY = {'pushd', 'pushd_parent', 'builtin_cd'}
_LAST_ONLY = {'pushd_after_pipeline', 'builtin_after_pipeline', 'read_pipeline', 'plain_pipeline'}

@pytest.mark.parametrize('remote', [False, True], ids=['local', 'ssh-login'])
@pytest.mark.parametrize('shell', ['bash', 'dash', 'zsh', 'lastpipe'])
@pytest.mark.parametrize('key', list(REAL_BODIES))
def test_real_shell_followups(tmp_path, monkeypatch, key, shell, remote):
    zsh = os.environ.get('FOLLOWUP_ZSH') or shutil.which('zsh')
    binary = zsh if shell == 'zsh' else shutil.which('bash' if shell == 'lastpipe' else shell)
    if not binary:
        pytest.skip(f'{shell} not installed; real-shell gate requires a separate full matrix')
    binary = str(Path(binary).resolve())
    home = tmp_path / 'user'
    live = home / '.hermes'
    live.mkdir(parents=True)
    (live / 'state.db').write_text('sentinel')
    (live / 'kanban.db').write_text('sentinel')
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    fake = bin_dir / 'rm'
    fake.write_text('#!' + sys.executable + '\n' + _FAKE_RM)
    fake.chmod(0o700)
    # Behaves like sshd: concatenate remote argv; run the configured login shell
    # in HOME. Never invokes real ssh or a remote host.
    ssh = bin_dir / 'ssh'
    ssh.write_text('#!' + sys.executable + '\n' +
                   'import os, subprocess, sys\n' +
                   "raise SystemExit(subprocess.call([os.environ['LOGIN_SHELL'], *os.environ['LOGIN_FLAGS'].split(), '-c', ' '.join(sys.argv[2:])], cwd=os.environ['HOME']))\n")
    ssh.chmod(0o700)
    receipt = tmp_path / 'rm.jsonl'
    env = dict(os.environ, HOME=str(home), HERMES_HOME=str(live),
               PATH=str(bin_dir) + os.pathsep + os.environ['PATH'],
               RM_RECEIPT=str(receipt), LOGIN_SHELL=binary,
               LOGIN_FLAGS='-O lastpipe' if shell == 'lastpipe' else '-f' if shell == 'zsh' else '')
    for name in ('BASH_ENV', 'ENV', 'ZDOTDIR', 'CDPATH', 'OLDPWD'):
        env.pop(name, None)
    flags = shlex.split(env['LOGIN_FLAGS'])
    body = REAL_BODIES[key]
    command = [str(ssh), 'example.com', body] if remote else [binary, *flags, '-c', body]
    result = subprocess.run(command, cwd=home, env=env, text=True, capture_output=True, timeout=8)
    rows = [json.loads(line) for line in receipt.read_text().splitlines()] if receipt.exists() else []
    hit = any(Path(row['cwd'], arg).resolve() in {live, live / 'state.db', live / 'kanban.db'}
              for row in rows for arg in row['args'] if arg and not arg.startswith('-'))
    expected = (shell != 'dash' if key in _PARENT_ONLY else
                shell in {'zsh', 'lastpipe'} if key in _LAST_ONLY else True)
    # zsh reports the output error but does not fail cd, so the || tail is skipped.
    if shell == 'zsh' and key in {'oldpwd', 'cdpath'}:
        expected = False
    assert hit == expected, {'key': key, 'shell': shell, 'remote': remote, 'rows': rows,
                             'stdout': result.stdout, 'stderr': result.stderr, 'rc': result.returncode}
    assert (live / 'state.db').read_text() == 'sentinel'
    assert (live / 'kanban.db').read_text() == 'sentinel'
