"""Managed-install fixtures are protocol coverage, not installed fleet evidence."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest
from _load import load

binding = load('runtime_binding')
LAUNCHER = Path(__file__).resolve().parents[1] / 'bin/hermes-pool-llm'


def fixture_runtime(tmp_path, python=None):
    root = tmp_path / 'managed-core'
    (root / 'hermes_cli').mkdir(parents=True)
    # Data-only publication fixture with real -I, module dispatch and exec.
    program = ("import sys,runpy,re,sqlite3,_sqlite3\n"
               "def deny(*a,**k): raise RuntimeError('SQL prohibited')\n"
               "sqlite3.connect=_sqlite3.connect=deny\n"
               "from hermes_cli.main import main\n"
               "sys.argv[0] = re.sub(r'(-script\\.pyw|\\.exe)?$', '', sys.argv[0])\n"
               "sys.exit(main())\n")
    (root / 'hermes_cli/_launch_template.py').write_text('def render(root, module, func):\n    return ' + repr(program) + '\n')
    (root / 'hermes_cli/_early_recovery.py').write_text('# fixture recovery; never imported\n')
    (root / 'hermes_cli/_launchers.py').write_text('# fixture publisher, no live bootstrap\n')
    (root / 'hermes_bootstrap.py').write_text(
        "import sqlite3,_sqlite3\n"
        "def deny(*a,**k): raise RuntimeError('SQL prohibited')\n"
        "sqlite3.connect=_sqlite3.connect=deny\n")
    def git(*args):
        return subprocess.check_output(['/usr/bin/git', '-C', str(root), *args], text=True).strip()
    git('init', '-q')
    git('add', 'hermes_cli', 'hermes_bootstrap.py')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.com', 'commit', '-qm', 'fixture')
    launcher = root / '.hermes/bin/hermes'
    launcher.parent.mkdir(parents=True)
    python = tmp_path / 'tools/python-fixture/bin/python3'
    python.parent.mkdir(parents=True)
    python.symlink_to(Path(sys.executable).resolve())
    python.parents[2].joinpath('facts.json').write_text(json.dumps({'packages': {'python': {'entry': 'python-fixture'}}}))
    launcher.write_text(binding.shell([str(python), '-I', '-c', program]))
    launcher.chmod(0o700)
    convenience = tmp_path / '.local/bin/hermes'
    convenience.parent.mkdir(parents=True)
    convenience.write_text(binding.shell([str(launcher)]))
    convenience.chmod(0o700)
    env = dict(os.environ, HOME=str(tmp_path), HERMES_HOME=str(tmp_path / 'home'), PYTHONDONTWRITEBYTECODE='1')
    return root, launcher, env


def launch(env, *args, launcher=LAUNCHER):
    return subprocess.run([str(launcher), *args], env=env,
                          capture_output=True, text=True, timeout=20)


def test_path_discovery_extra_prelude_never_executes(tmp_path):
    root, managed, env = fixture_runtime(tmp_path)
    bad = tmp_path / 'bad'
    bad.mkdir()
    marker = tmp_path / 'executed'
    code = (f"from pathlib import Path; Path({str(marker)!r}).touch(); "
            "import runpy,sys; runpy.run_module('hermes_cli.main', run_name='__main__', alter_sys=True)")
    shim = bad / 'hermes'
    shim.write_text(f'#!{sys.executable}\nimport json\nprint(json.dumps({[sys.executable, "-c", code]!r}))\n')
    shim.chmod(0o700)
    env['PATH'] = str(bad) + os.pathsep + env.get('PATH', '')
    p = launch(env, 'env', launcher=Path(os.environ.get('POOL_TEST_LAUNCHER', LAUNCHER)))
    assert not marker.exists(), 'PATH-selected arbitrary prelude executed'
    assert p.returncode == 0, p.stderr


@pytest.mark.parametrize('mutation', ['shell-prelude', 'python-prelude', 'flags', 'dirty-template'])
def test_noncanonical_launchers_refused_before_exec(tmp_path, mutation):
    root, launcher, env = fixture_runtime(tmp_path)
    marker = tmp_path / 'marker'
    if mutation == 'shell-prelude':
        launcher.write_text(launcher.read_text().replace('exec ', f'touch {shlex.quote(str(marker))}\nexec ', 1))
    elif mutation in ('python-prelude', 'flags'):
        command = binding.parse_shell(launcher)
        if mutation == 'flags':
            command.insert(1, '-S')
        else:
            command[3] = f'open({str(marker)!r}, "w").close()\n' + command[3]
        launcher.write_text(binding.shell(command))
    else:
        path = root / 'hermes_cli/_launch_template.py'
        path.write_text(path.read_text() + '\n# dirty\n')
    p = launch(env, 'env')
    assert p.returncode == 2
    assert not marker.exists()
    assert not (Path(env['HERMES_HOME']) / 'pool-llm/serve.token').exists()


def test_routine_core_interpreter_and_environment_update_needs_no_provisioning(tmp_path):
    root, launcher, env = fixture_runtime(tmp_path)
    first = launch(env, 'env')
    assert first.returncode == 0, first.stderr
    # Ordinary publisher replaces its Python pin, with an unrelated core commit.
    replacement = tmp_path / 'tools/python-next/bin/python3'
    replacement.parent.mkdir(parents=True)
    replacement.symlink_to(Path(sys.executable).resolve())
    replacement.parents[2].joinpath('facts.json').write_text(json.dumps({'packages': {'python': {'entry': 'python-next'}}}))
    command = binding.parse_shell(launcher)
    command[0] = str(replacement)
    launcher.write_text(binding.shell(command))
    (root / 'unrelated.py').write_text('VERSION = 2\n')
    subprocess.check_call(['/usr/bin/git', '-C', str(root), 'add', 'unrelated.py'])
    subprocess.check_call(['/usr/bin/git', '-C', str(root), '-c', 'user.name=Fixture',
                           '-c', 'user.email=fixture@example.com', 'commit', '-qm', 'routine core update'])
    second = launch(env, 'env')
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout
    assert not (Path(env['HERMES_HOME']) / 'pool-llm/runtime.json').exists()


def test_binding_read_is_offline_and_does_not_execute_core(tmp_path):
    root, launcher, env = fixture_runtime(tmp_path)
    marker = tmp_path / 'core-imported'
    # Even committed module-level side effects are not evaluated by resolution.
    path = root / 'hermes_cli/_early_recovery.py'
    path.write_text(f'open({str(marker)!r}, "w").close()\n')
    subprocess.check_call(['/usr/bin/git', '-C', str(root), 'add', '.'])
    subprocess.check_call(['/usr/bin/git', '-C', str(root), '-c', 'user.name=Fixture',
                           '-c', 'user.email=fixture@example.com', 'commit', '-qm', 'side effect control'])
    p = launch(env, 'binding')
    assert p.returncode == 0, p.stderr
    receipt = json.loads(p.stdout)
    assert receipt['core'] == str(root)
    assert Path(receipt['python']).resolve() == Path(sys.executable).resolve()
    assert not marker.exists()
    assert not Path(env['HERMES_HOME']).exists()


def test_unrecorded_interpreter_refused(tmp_path):
    root, launcher, env = fixture_runtime(tmp_path)
    selected = binding.parse_shell(launcher)
    selected[0] = str(Path(sys.executable).resolve())
    launcher.write_text(binding.shell(selected))
    p = launch(env, 'env')
    assert p.returncode == 2
    assert not (Path(env['HERMES_HOME']) / 'pool-llm/serve.token').exists()


def test_ambient_runtime_override_cannot_retarget_published_selection(tmp_path):
    root, launcher, env = fixture_runtime(tmp_path)
    env['HERMES_RUNTIME_DIR'] = str(tmp_path / 'foreign-runtime')
    env['HERMES_POOL_LLM_BINDING'] = str(tmp_path / 'untrusted.json')
    p = launch(env, 'binding')
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)['python'] == binding.parse_shell(launcher)[0]


def test_initial_path_python_and_site_hooks_never_execute(tmp_path):
    _, _, env = fixture_runtime(tmp_path)
    poison = tmp_path / 'ambient'
    poison.mkdir()
    marker = tmp_path / 'ambient-executed'
    fake = poison / 'python3'
    fake.write_text('#!/bin/sh\n: > ' + shlex.quote(str(marker)) + '\nexit 0\n')
    fake.chmod(0o700)
    (poison / 'sitecustomize.py').write_text(f'open({str(marker)!r}, "w").close()\n')
    env.update(PATH=str(poison) + os.pathsep + env.get('PATH', ''),
               PYTHONPATH=str(poison), PYTHONHOME=str(poison))
    target = Path(os.environ.get('POOL_INITIAL_LAUNCHER', LAUNCHER))
    p = launch(env, 'binding', launcher=target)
    assert not marker.exists(), 'ambient Python or startup hook executed'
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)['version'] == 1


def test_symlinked_pool_launcher_resolves_plugin_sibling(tmp_path):
    _, _, env = fixture_runtime(tmp_path)
    link = tmp_path / 'pool'
    link.symlink_to(LAUNCHER)
    p = launch(env, 'env', launcher=link)
    assert p.returncode == 0, p.stderr


@pytest.mark.parametrize('argument', ['--run-module', '--print-runtime-command'])
def test_hermes_dispatch_arguments_cannot_execute_managed_prelude(tmp_path, argument):
    root, launcher, env = fixture_runtime(tmp_path)
    marker = tmp_path / 'hermes-dispatch-executed'
    selected = binding.parse_shell(launcher)
    dispatch = (f"if sys.argv[1:2] == [{argument!r}]:\n"
                f" open({str(marker)!r}, 'w').close()\n"
                " raise SystemExit(0)\n")
    selected[3] = selected[3].replace('from hermes_cli.main import main\n', dispatch + 'from hermes_cli.main import main\n')
    launcher.write_text(binding.shell(selected))
    (root / 'hermes_cli/_launch_template.py').write_text('def render(root,module,func):\n return ' + repr(selected[3]) + '\n')
    subprocess.check_call(['/usr/bin/git', '-C', str(root), 'add', '.'])
    subprocess.check_call(['/usr/bin/git', '-C', str(root), '-c', 'user.name=Fixture',
                           '-c', 'user.email=fixture@example.com', 'commit', '-qm', 'dispatch fixture'])
    target_binding = binding
    if os.environ.get('POOL_COMMAND_BINDING'):
        import importlib.util
        spec = importlib.util.spec_from_file_location('prior_binding', os.environ['POOL_COMMAND_BINDING'])
        target_binding = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(target_binding)
    old_home = os.environ.get('HOME')
    try:
        os.environ['HOME'] = str(tmp_path)
        cmd = target_binding.command(LAUNCHER.parents[1] / 'pool_llm.py', [argument, 'arbitrary'])
    finally:
        if old_home is None:
            os.environ.pop('HOME', None)
        else:
            os.environ['HOME'] = old_home
    p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=20)
    assert not marker.exists(), 'managed CLI branch escaped pool dispatcher'
    assert p.returncode != 0, p.stdout


def test_renderer_rejects_executable_expressions():
    with pytest.raises(binding.BindingError):
        binding.render('def render(root,module,func):\n return __import__("os").system("false")', '', Path('/fixture'))
