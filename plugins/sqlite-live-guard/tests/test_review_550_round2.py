"""Regression families from admitted review 6094771739."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
import pytest
from test_review_550 import HOME, guard, lexical_home

CASES = [
    'nice -n 5 env -C ~/.hermes rm -rf state.db',
    'timeout -s KILL 5 env -C ~/.hermes rm -rf state.db',
    'ionice -c 3 env -C ~/.hermes rm -rf state.db',
    'setsid env -C ~/.hermes rm -rf state.db',
    'env -C ~/.hermes nice -n 5 rm -rf state.db',
    "printf 'state.db\\n' | env -C ~/.hermes xargs rm",
    'xargs env -C ~/.hermes rm -rf state.db',
    'env -C ~/.hermes busybox rm -rf state.db',
    '''env -C ~/.hermes python3 -c "import os; os.remove('state.db')"''',
    'printf -vD %s ~/.hermes; cd "$D"; rm -rf state.db',
    'printf -vD %s ~/.hermes; rm -rf "$D/state.db"',
    'set ~/.hermes; cd "$1"; rm -rf state.db',
    'set ~/.hermes; rm -rf "$1/state.db"',
    'OLDPWD=~/.hermes; D=~-; cd $D; rm -rf state.db',
    'cd ~/.hermes; D=~+; cd /; rm -rf "$D/state.db"',
    "cd ~/.hermes; cd /; cd $OLDPWD && sqlite3 kanban.db 'delete from tasks'",
    'cd ~/.hermes; cd /; rm -rf "$OLDPWD/state.db"',
    'cd ~/.hermes; X=$PWD; cd /; rm -rf "$X/state.db"',
    'mapfile -t A <<< ~/.hermes; rm -rf "${A[0]}/state.db"',
    'readarray -t A <<< ~/.hermes; rm -rf "${A[0]}/state.db"',
    r"setsid env -C ~/.hermes -S 'rm\_-rf\_state.db'",
]

@pytest.mark.parametrize('body', CASES)
@pytest.mark.parametrize('wrapper', ['local','bash','ssh'])
def test_review_round2_shell(body, wrapper):
    command = body if wrapper == 'local' else ('bash -c ' if wrapper == 'bash' else 'ssh example.com ') + shlex.quote(body)
    assert guard.check_shell(command, HOME), command


def test_join_analysis_shared_budget(tmp_path):
    # Executes only the analyzer, never the Python source being analyzed.
    n = 800
    code = 'import os,shutil; d="x"; c=os.path.join('+','.join(['d']*n)+'); b=os.path.join('+','.join(['c']*n)+'); shutil.rmtree(os.path.join('+','.join(['b']*n)+'))'
    helper = tmp_path/'probe.py'
    helper.write_text('import importlib.util,json,time\n'+
        f's=importlib.util.spec_from_file_location("bounded_guard",{str(Path(guard.__file__).resolve())!r})\n'+
        'g=importlib.util.module_from_spec(s); s.loader.exec_module(g)\n'+
        'steps=[0]; original=getattr(g,"_py_directory_literal",None)\n'+
        'def counted(*a,**kw):\n    steps[0]+=1\n    return original(*a,**kw)\n'+
        'if original is not None: g._py_directory_literal=counted\n'+
        f't=time.monotonic(); why=g.check_python({code!r}); print(json.dumps([why,time.monotonic()-t,steps[0]]))\n')
    try:
        result = subprocess.run([sys.executable,str(helper)],capture_output=True,text=True,timeout=8)
    except subprocess.TimeoutExpired:
        pytest.fail('path analyzer exceeded 8s on 4.9KB n-ary join input')
    assert result.returncode == 0, result.stderr
    why, elapsed, steps = json.loads(result.stdout)
    assert why and 'budget' in why, (why,elapsed)
    assert 1 <= steps <= 8193, steps  # deterministic work bound, independent of CPU speed
    assert elapsed < 3, elapsed


def test_round2_differential():
    rows = json.loads((Path(__file__).parent/'fixtures/review_550_round2_main.json').read_text())
    commands = {body if wrapper == 'local' else ('bash -c ' if wrapper == 'bash' else 'ssh example.com ') + shlex.quote(body)
                for body in CASES for wrapper in ['local','bash','ssh']}
    assert commands == {row['command'] for row in rows}
    now = [bool(guard.check_shell(row['command'], HOME)) for row in rows]
    assert all(now)
    assert all(not row['main_refused'] or verdict for row,verdict in zip(rows,now))
    assert all(not row['prior_refused'] or verdict for row,verdict in zip(rows,now))
    print(f"round2 corpus={len(rows)} main={sum(r['main_refused'] for r in rows)} prior={sum(r['prior_refused'] for r in rows)} head={sum(now)} newly_allowed=0")


def test_full_prior_head_floor(monkeypatch):
    rows = json.loads((Path(__file__).parent/'fixtures/pr550_prior_heads.json').read_text())['rows']
    refused = 0
    for row in rows:
        monkeypatch.setenv('HOME',row['home'])
        monkeypatch.setenv('HERMES_HOME',row['home']+'/.hermes')
        why = guard.check_python(row['code']) if 'code' in row else guard.check_shell(row['command'],row['cwd'])
        refused += bool(why)
        assert not (row['main'] or row['a7b965fb'] or row['0b04e6b0']) or why, row
    print(f"prior-head corpus={len(rows)} main={sum(r['main'] for r in rows)} a7b965fb={sum(r['a7b965fb'] for r in rows)} 0b04e6b0={sum(r['0b04e6b0'] for r in rows)} head={refused} newly_allowed=0")


def test_round2_control():
    for command in ['env -S "echo hi"','printf -vD %s hi','set a b','mapfile -t A <<< x; echo ok']:
        assert guard.check_shell(command,HOME) is None
