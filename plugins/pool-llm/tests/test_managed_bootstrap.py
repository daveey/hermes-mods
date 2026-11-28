"""Real Git-bound bootstrap/PM activation/relaunch with controlled external owners.

Opt in with POOL_CORE_SOURCE pointing to a core checkout. No core imports occur in
this test process. All state is disposable, SQL fenced, repair/provider/account
owners replaced explicitly. This is not an installed or live-provider proof.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from _load import load

binding = load('runtime_binding')


def test_real_managed_bootstrap_reexec_and_dependency_activation(tmp_path, monkeypatch):
    source = os.environ.get('POOL_CORE_SOURCE')
    if not source:
        pytest.skip('explicit Git-bound core source required; no core in public CI')
    source = Path(source)
    root = tmp_path / 'core'
    root.mkdir()
    identities = {}
    commit = binding.git(source, 'rev-parse', 'HEAD').decode().strip()
    for relative in ('hermes_bootstrap.py', 'hermes_cli/_launch_template.py',
                     'hermes_cli/_early_recovery.py', 'hermes_cli/_launchers.py',
                     'pm/environments.py', 'hermes_cli/venv_sync.py'):
        data = binding.git(source, 'show', commit + ':' + relative)
        assert data == (source / relative).read_bytes()
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        identities[relative] = hashlib.sha256(data).hexdigest()
    # Extract the real relaunch function without executing venv_sync's unrelated
    # module imports. The entire original source identity is retained above.
    text = (root / 'hermes_cli/venv_sync.py').read_text()
    node = next(n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef) and n.name == 'relaunch_command')
    function = ast.get_source_segment(text, node)
    (root / 'relaunch_function.txt').write_text(function)
    home = tmp_path / 'home'
    state = home / 'installs' / hashlib.sha256(str(root).encode()).hexdigest()[:16]
    generation = state / 'environments' / 'test' / 'venv'
    site = generation / 'lib' / ('python%d.%d' % sys.version_info[:2]) / 'site-packages'
    site.mkdir(parents=True)
    (generation / 'pyvenv.cfg').write_text('include-system-site-packages = false\n')
    (state / 'facts.json').write_text(json.dumps({'packages': {'venv': {'environment': str(generation)}}}))
    # .pth is intentionally executable: proves actual site.addsitedir ran, not
    # merely that a selected-environment path appeared in the fixture report.
    (site / 'proof.pth').write_text('import os; os.environ["POOL_PTH_EXECUTED"] = "yes"\n')
    (root / 'pm/__init__.py').write_text('')
    (root / 'hermes_constants.py').write_text(
        'import os\nfrom pathlib import Path\n'
        'def get_default_hermes_root(home=None): return Path(home or os.environ["HERMES_HOME"])\n'
        'def get_hermes_home_override(): return os.environ.get("HERMES_HOME")\n'
        'def project_venv_dir(root): return root / ".venv"\n'
        'def export_scratch_tmp_env(): pass\n')
    (root / 'pm/dependency_imports.py').write_text('def select_wheel_sources(*a): pass\n')
    # hermes_cli import is the first managed package import in real bootstrap.
    # Install write/provider/SQL fences before its repair/observation/PM stages.
    setup = '''import sqlite3, _sqlite3, sys, os, types, contextlib
from pathlib import Path
def deny(*a, **k): raise RuntimeError('SQL/provider/repair prohibited')
sqlite3.connect = _sqlite3.connect = deny
def module(name, **values):
 m = types.ModuleType(name)
 m.__dict__.update(values)
 sys.modules[name] = m
 return m
module('hermes_cli._early_recovery', restore_interrupted_pull=lambda *a: False,
       recover_if_needed=lambda *a: None)
module('hermes_cli.git_fingerprint', record_process_boot_fingerprint=lambda *a: None,
       process_boot_fingerprint=lambda *a: 'fixture:revision')
module('hermes_cli.code_attestation', install=lambda *a: None, route_report=lambda: {'route':'fixture'},
       register_dependency_environment=lambda *a: None, defer_comparisons=lambda: None)
module('hermes_cli.local_work', install=lambda: None)
module('hermes_agent', __path__=[])
module('hermes_agent.compat', install=lambda: None)
module('hermes_cli._parser', command_argv=lambda a: a)
module('hermes_cli.launch_malloc', MARK='FIXTURE_MALLOC', ensure=lambda *a: None)
module('hermes_cli.runtime_state', runtime_lock=lambda *a: contextlib.nullcontext(True),
       recover_publication=lambda *a: None, lease_generation=lambda *a: lambda: None)
module('hermes_cli.proof_home', prepare_backend_argv=lambda: None)
root = Path(__file__).parent.parent
venv = module('hermes_cli.venv_sync', Path=Path)
exec((root / 'relaunch_function.txt').read_text(), venv.__dict__)
def prepare(*a):
 return None if getattr(sys, '_hermes_relaunched', False) else Path(os.environ['POOL_NEXT_PYTHON'])
def exec_launch(python, command, argv):
 os.execv(str(python), command)
venv.prepare_launch = prepare
venv.exec_launch = exec_launch
'''
    (root / 'hermes_cli/__init__.py').write_text(setup)
    # Verify no credential/model adapter can be imported accidentally.
    (root / 'agent').mkdir()
    (root / 'agent/__init__.py').write_text("raise RuntimeError('live adapter import forbidden')\n")
    git = ['/usr/bin/git', '-C', str(root)]
    subprocess.check_call([*git, 'init', '-q'])
    subprocess.check_call([*git, 'add', '.'])
    subprocess.check_call([*git, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.com',
                           'commit', '-qm', 'Git-bound bootstrap fixture with explicit controlled owners'])
    first = tmp_path / 'tools/python-first/bin/python3'
    second = tmp_path / 'tools/python-second/bin/python3'
    for python in (first, second):
        python.parent.mkdir(parents=True)
        python.symlink_to(Path(sys.executable).resolve())
    (tmp_path / 'tools/facts.json').write_text(json.dumps({'packages': {'python': {'entry': 'python-first'}}}))
    template = (root / 'hermes_cli/_launch_template.py').read_bytes()
    recovery = binding.git(source, 'show', commit + ':hermes_cli/_early_recovery.py')
    launcher = root / '.hermes/bin/hermes'
    launcher.parent.mkdir(parents=True)
    launcher.write_text(binding.shell([str(first), '-I', '-c', binding.render(template, recovery, root)]))
    forward = tmp_path / '.local/bin/hermes'
    forward.parent.mkdir(parents=True)
    forward.write_text(binding.shell([str(launcher)]))
    probe = tmp_path / 'probe.py'
    probe.write_text('import os,sys,json,sqlite3,_sqlite3\n'
                     'assert os.environ["POOL_PTH_EXECUTED"] == "yes"\n'
                     'for connect in (sqlite3.connect, _sqlite3.connect):\n'
                     ' try: connect(":memory:")\n'
                     ' except RuntimeError: pass\n'
                     ' else: raise AssertionError("SQL fence absent")\n'
                     'print(json.dumps({"reexec":sys._hermes_relaunched,"argv":sys.argv[1:],'
                     '"selected":sys.path}))\n')
    monkeypatch.setenv('HOME', str(tmp_path))
    command = binding.command(probe, ['models'])
    env = dict(os.environ, HERMES_HOME=str(home), HERMES_PM_ACCOUNT_SEED='0',
               POOL_NEXT_PYTHON=str(second), PYTHONDONTWRITEBYTECODE='1')
    child = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    assert child.returncode == 0, child.stderr
    result = json.loads(child.stdout)
    assert result['reexec'] is True
    assert result['argv'] == ['models']
    assert str(site) in result['selected']
    print('Git-bound source identities:', json.dumps(identities, sort_keys=True))
