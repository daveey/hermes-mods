"""Real GNU env/state proofs. All mutation binaries are absolute recording shims."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import pytest

BODIES = {
    **{f'env-{option}': f'/usr/bin/env {option} "$HOME/.hermes" {{rm}} -rf state.db'
       for option in ['-C', '-iC', '--c', '--ch', '--chd', '--chdi', '--chdir']},
    'env-cp': '/usr/bin/env -C "$HOME/.hermes" {cp} /dev/null state.db',
    'env-sqlite': '/usr/bin/env -C "$HOME/.hermes" {sqlite3} kanban.db "delete from tasks"',
    'env-split': '/usr/bin/env -S "-C $HOME/.hermes {rm} -rf state.db"',
    'env-long-split': '/usr/bin/env --split-string="-C $HOME/.hermes {rm} -rf state.db"',
    'pushd-sqlite': 'pushd ~/.hermes; {sqlite3} kanban.db "delete from tasks"',
    'pushd-find': 'pushd ~/.hermes; {find} . -name state.db -delete',
    'explicit-find': '{find} ~/.hermes -name state.db -delete',
    'tilde-minus': 'OLDPWD=~/.hermes; cd ~- && {rm} -rf state.db',
    'tilde-plus': 'cd ~/.hermes; cd ~+; {rm} -rf state.db',
    'tilde-stack': 'pushd ~/.hermes; pushd ~; cd ~1 && {rm} -rf state.db',
    'tilde-plus-stack': 'pushd ~/.hermes; pushd ~; cd ~+1 && {rm} -rf state.db',
    'tilde-minus-stack': 'pushd ~/.hermes; pushd ~; cd ~-1 && {rm} -rf state.db',
    'chdir': 'chdir ~/.hermes; {rm} -rf state.db',
    'printf': 'printf -v D %s ~/.hermes; cd "$D"; {rm} -rf state.db',
    'positional': 'set -- ~/.hermes; cd "$1"; {rm} -rf state.db',
    'positional-direct': 'set -- ~/.hermes; {rm} -rf "$1"',
    'printf-direct': 'printf -v D %s ~/.hermes; {rm} -rf "$D/state.db"',
}

@pytest.mark.parametrize('remote', [False, True], ids=['local','ssh-login'])
@pytest.mark.parametrize('shell', ['bash','dash','zsh','lastpipe'])
@pytest.mark.parametrize('key', BODIES)
def test_review_real_shell(tmp_path, shell, remote, key):
    binary = (os.environ.get('FOLLOWUP_ZSH') or shutil.which('zsh')) if shell == 'zsh' else shutil.which('bash' if shell == 'lastpipe' else shell)
    if not binary:
        pytest.skip(f'{shell} unavailable')
    home = tmp_path/'home'
    live = home/'.hermes'
    live.mkdir(parents=True)
    for name in ['state.db','kanban.db']:
        (live/name).write_text('sentinel')
    receipt = tmp_path/'receipt.jsonl'
    bins = {}
    for name in ['rm','cp','sqlite3','find']:
        fake = tmp_path/name
        fake.write_text('#!'+sys.executable+'\nimport os,json,sys\n'+
                        f'with open({str(receipt)!r}, "a") as f: f.write(json.dumps({{"cwd":os.getcwd(),"args":sys.argv[1:]}})+"\\n")\n')
        fake.chmod(0o700)
        bins[name] = shlex.quote(str(fake))
    flags = ['-O','lastpipe'] if shell == 'lastpipe' else ['-f'] if shell == 'zsh' else []
    ssh = tmp_path/'ssh'
    ssh.write_text('#!'+sys.executable+'\nimport subprocess,sys\n'+
                   f'raise SystemExit(subprocess.call({[binary,*flags,"-c"]!r}+[" ".join(sys.argv[2:])],cwd={str(home)!r}))\n')
    ssh.chmod(0o700)
    env = dict(os.environ, HOME=str(home), HERMES_HOME=str(live))
    for name in ['BASH_ENV','ENV','ZDOTDIR','CDPATH','OLDPWD']:
        env.pop(name,None)
    body = BODIES[key].format(**bins)
    command = [str(ssh),'example.com',body] if remote else [binary,*flags,'-c',body]
    result = subprocess.run(command,env=env,cwd=home,text=True,capture_output=True,timeout=8)
    rows = [json.loads(line) for line in receipt.read_text().splitlines()] if receipt.exists() else []
    hit = any(Path(row['cwd'],arg).resolve() in {live,live/'state.db',live/'kanban.db'}
              for row in rows for arg in row['args'] if arg and not arg.startswith('-'))
    expected = True
    if key.startswith(('pushd-', 'printf')) or key in {'tilde-stack','tilde-plus-stack','tilde-minus-stack'}:
        expected = shell != 'dash'
    if key == 'chdir':
        expected = shell in {'zsh', 'dash'}
    # bash expands ~- from OLDPWD; dash leaves it literal and zsh uses its stack.
    if key == 'tilde-minus':
        expected = shell in {'bash', 'lastpipe'}
    assert hit == expected, (key,shell,remote,rows,result.stderr,result.returncode)
    assert all((live/name).read_text() == 'sentinel' for name in ['state.db','kanban.db'])
