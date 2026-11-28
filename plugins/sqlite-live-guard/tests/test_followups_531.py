"""Every concrete PR-531 follow-up, plus a post-merge differential floor."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import followup_corpus as corpus

spec = importlib.util.spec_from_file_location('followup_guard', HERE.parent / 'guard.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
BASELINE = json.loads((HERE / 'fixtures/followup_main_verdicts.json').read_text())

@pytest.fixture(autouse=True)
def lexical_home(monkeypatch):
    monkeypatch.setenv('HOME', corpus.HOME)
    monkeypatch.setenv('HERMES_HOME', corpus.HOME + '/.hermes')

@pytest.mark.parametrize('row', corpus.shell_cases(), ids=lambda r: r['id'])
def test_followup_shell_refused(row):
    assert guard.check_shell(row['command'], row['cwd']), row

@pytest.mark.parametrize('key,code', list(corpus.PYTHON.items()))
def test_followup_python_refused(key, code):
    assert guard.check_python(code), key

@pytest.mark.parametrize('command', [
    'pushd /tmp; ls', 'builtin cd /tmp; pwd', 'echo x | read D; echo "$D"',
    'OLDPWD=/tmp; cd -; pwd', 'CDPATH=/tmp cd x; pwd',
    'source /dev/null; echo ok', 'f() { echo ok; }; f',
    'echo "rm state.db"', "echo 'pushd ~/.hermes; rm state.db'",
    'ssh example.com "echo rm state.db"', 'rm -rf /tmp/disposable',
    'ssh example.com "cd /tmp && rm -rf .hermes"',
])
def test_followup_readonly_and_explicit_controls(command):
    assert guard.check_shell(command, corpus.HOME) is None

@pytest.mark.parametrize('code', [
    "open('state.db').read()", "open('state.db', mode='r').read()",
    "open('/tmp/state.db', 'w').write('')", "import os; os.remove('cache/state.db')",
    "import shutil; shutil.rmtree('/tmp/disposable')",
    "import os, shutil; shutil.rmtree(os.path.expanduser('~/.hermes/cache'))",
    "from pathlib import Path; import shutil; shutil.rmtree(Path.home()/'.hermes'/'cache')",
    "print(\"open('state.db', 'w')\")",
])
def test_followup_python_controls(code):
    assert guard.check_python(code) is None

@pytest.mark.parametrize('command', [
    'popd; rm -f /tmp/x', 'source /tmp/script; rm -f /tmp/x',
    '. /tmp/script; rm -f /tmp/x', 'function f { :; }; rm -f /tmp/x',
    'cd -; rm -f /tmp/x', 'CDPATH=/tmp cd x; rm -f /tmp/x',
    'env --chdir=/tmp rm -f x', 'env -C/tmp rm -f x',
])
def test_followup_unsupported_state_is_class_level(command):
    assert guard.check_shell(command, corpus.HOME)


@pytest.mark.parametrize('code', [
    "open(file='state.db', mode='w')", "import io; io.open('state.db', 'a')",
    "from pathlib import Path; import shutil; p=Path.home()/'.hermes'; shutil.rmtree(p)",
    "import os, shutil; p=os.fspath('~/.hermes'); shutil.rmtree(p)",
])
def test_followup_python_variants(code):
    assert guard.check_python(code)


def test_post531_differential():
    now = []
    for row in BASELINE:
        why = (guard.check_python(row['code']) if 'code' in row else
               guard.check_shell(row['command'], row['cwd']))
        now.append(bool(why))
        assert not row['refused'] or why, row
    print(f"post531 corpus={len(BASELINE)} main_refused={sum(r['refused'] for r in BASELINE)} "
          f"head_refused={sum(now)} newly_allowed=0")
