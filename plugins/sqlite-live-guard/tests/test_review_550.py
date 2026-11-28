"""Admitted review 6094568771: regression families, not arbitrary new scope."""
import importlib.util
import json
import shlex
from pathlib import Path
import pytest

HOME = '/home/sqlite-followup-fixture'
spec = importlib.util.spec_from_file_location('review550_guard', Path(__file__).resolve().parents[1] / 'guard.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

@pytest.fixture(autouse=True)
def lexical_home(monkeypatch):
    monkeypatch.setenv('HOME', HOME)
    monkeypatch.setenv('HERMES_HOME', HOME + '/.hermes')

ENV_OPTIONS = ['-C ~/.hermes', '-C~/.hermes', '-iC ~/.hermes',
               *[f'{"--chdir"[:n]} ~/.hermes' for n in range(3, 8)],
               '--chdir=~/.hermes', "-S '-C ~/.hermes rm -rf state.db'",
               "--split-string='-C ~/.hermes rm -rf state.db'"]
ENV_CASES = [f'{exe} {opt}' + ('' if 'split-string' in opt or opt.startswith('-S') else f' {mutation}')
             for exe in ['env', '/usr/bin/env'] for opt in ENV_OPTIONS
             for mutation in ['rm -rf state.db', 'cp /dev/null state.db', "sqlite3 kanban.db 'delete from tasks'"]]
STATE_CASES = [f'{prefix}; {mutation}'
               for prefix in ['pushd ~/.hermes', 'chdir ~/.hermes', 'OLDPWD=~/.hermes; cd ~-',
                              'cd ~+', 'cd ~1', 'cd ~+1', 'cd ~-1',
                              "printf -v D %s ~/.hermes; cd \"$D\"", 'set -- ~/.hermes; cd "$1"']
               for mutation in ['rm -rf state.db', "sqlite3 kanban.db 'delete from tasks'", 'find . -name state.db -delete',
                                'cp /dev/null state.db', 'mv x state.db', ': > state.db']]
SHELL_CASES = ENV_CASES + STATE_CASES + [
    'find ~/.hermes -name state.db -delete', 'set -- ~/.hermes; rm -rf "$1"',
    'printf -v D %s ~/.hermes; rm -rf "$D/state.db"',
    'command /usr/bin/env -iC ~/.hermes rm -rf state.db',
    'sudo /usr/bin/env --ch ~/.hermes cp /dev/null state.db',
    'env -C ~/.hermes bash -c "rm -rf state.db"',
    r"env -S '-C /home/sqlite-followup-fixture/.hermes\_rm\_-rf\_state.db'",
    r"env --split-string='-C /home/sqlite-followup-fixture/.hermes rm\_-rf\_state.db'",
    r"env -S '${HOME}/bin/rm -rf ~/.hermes/state.db'",
    r"env -S '/usr/bin/printf\_hello'"]
PYTHON_CASES = [f'import os, shutil; from pathlib import Path; shutil.rmtree({expr})' for expr in [
    "Path('~/.hermes').expanduser()", "os.path.join(os.path.expanduser('~'), '.hermes')",
    "Path.home().joinpath('.hermes')", "os.path.expanduser('~') + '/.hermes'",
    "Path.home().joinpath('.hermes', 'cache', '..')"]] + [
    "from pathlib import Path; (Path.home()/'.hermes'/'state.db').unlink()",
    "import os; os.unlink(os.path.join(os.path.expanduser('~'), '.hermes', 'state.db'))",
    "from pathlib import Path; Path('~/.hermes/state.db').expanduser().unlink()"]

@pytest.mark.parametrize('body', SHELL_CASES)
@pytest.mark.parametrize('wrapper', ['local', 'bash', 'ssh'])
def test_review_shell(body, wrapper):
    command = body if wrapper == 'local' else ('bash -c ' if wrapper == 'bash' else 'ssh example.com ') + shlex.quote(body)
    assert guard.check_shell(command, HOME), command

@pytest.mark.parametrize('code', PYTHON_CASES)
def test_review_python(code):
    assert guard.check_python(code), code

@pytest.mark.parametrize('command', ['env -i ls', '/usr/bin/env --chdir=/tmp ls', 'echo "cd ~-; rm state.db"',
                                     'printf %s hello', 'set -- hello; echo "$1"'])
def test_review_nonwriting_controls(command):
    assert guard.check_shell(command, HOME) is None

def test_review_differential():
    fixture = json.loads((Path(__file__).parent / 'fixtures/review_550_main.json').read_text())
    rows = fixture['rows']
    expected = {body if wrapper == 'local' else ('bash -c ' if wrapper == 'bash' else 'ssh example.com ') + shlex.quote(body)
                for body in SHELL_CASES for wrapper in ['local', 'bash', 'ssh']}
    assert expected == {r['command'] for r in rows if 'command' in r}
    assert set(PYTHON_CASES) == {r['code'] for r in rows if 'code' in r}
    now = [bool(guard.check_python(r['code']) if 'code' in r else guard.check_shell(r['command'], HOME)) for r in rows]
    assert all(not r['refused'] or v for r, v in zip(rows, now))
    assert all(now)
    print(f"review550 corpus={len(rows)} main_refused={sum(r['refused'] for r in rows)} head_refused={sum(now)} newly_allowed=0")


@pytest.mark.parametrize('expr', ["Path('~/.hermes/cache').expanduser()", "Path.home().joinpath('.hermes','cache')",
                                  "os.path.join(os.path.expanduser('~'), '.hermes', 'cache')",
                                  "os.path.expanduser('~') + '/.hermes/cache'"])
def test_review_python_cache_control(expr):
    assert guard.check_python(f'import os, shutil; from pathlib import Path; shutil.rmtree({expr})') is None
