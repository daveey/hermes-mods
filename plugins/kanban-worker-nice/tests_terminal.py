"""Real shell checks; inherit any runner floor instead of requiring root."""
import importlib.util
import os
from pathlib import Path
import subprocess
import socket
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux orchestration-host policy')

spec = importlib.util.spec_from_file_location('nice_plugin', Path(__file__).with_name('__init__.py'))
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)


@pytest.fixture(autouse=True)
def chat(monkeypatch, tmp_path):
    monkeypatch.delenv('HERMES_KANBAN_TASK', raising=False)
    monkeypatch.delenv('HERMES_KANBAN_WORKER_TASK', raising=False)
    monkeypatch.delenv('HUB_NICE_OK', raising=False)
    monkeypatch.delenv('HERMES_KANBAN_WORKER_NICE', raising=False)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_TERMINAL_NICE_HOSTS', 'orchestrator')
    monkeypatch.setattr(socket, 'gethostname', lambda: 'orchestrator.example.com')


def run(command='nice', **args):
    original = dict(command=command, **args)
    result = plugin.terminal_request(tool_name='terminal', args=original)
    effective = result['args'] if result else original
    return int(subprocess.check_output(['bash', '-c', effective['command']], text=True))


@pytest.mark.parametrize('command,expected', [('nice', 10), ('HUB_NICE_OK=1 nice', 0)])
def test_zero_priority_subshell(command, expected):
    """Match the local terminal launcher: eval runs in a forked bash subshell."""
    if os.getpriority(os.PRIO_PROCESS, 0) != 0:
        pytest.skip('zero-priority regression requires an ordinary launcher')
    args = {'command': command}
    result = plugin.terminal_request(tool_name='terminal', args=args)
    effective = result['args'] if result else args
    import shlex
    script = '( eval ' + shlex.quote(effective['command']) + ' ); nice'
    output = subprocess.check_output(['bash', '-c', script], text=True)
    assert [int(n) for n in output.split()] == [expected, 0]
    assert os.getpriority(os.PRIO_PROCESS, 0) == 0


def test_listed_chat():
    assert run() == max(os.getpriority(os.PRIO_PROCESS, 0), 10)


@pytest.mark.parametrize('command', ['HUB_NICE_OK=1 nice', 'env HUB_NICE_OK=1 nice'])
def test_command_opt_out(command):
    assert run(command) == os.getpriority(os.PRIO_PROCESS, 0)


def test_unlisted(monkeypatch):
    monkeypatch.setenv('HERMES_TERMINAL_NICE_HOSTS', 'other')
    assert run() == os.getpriority(os.PRIO_PROCESS, 0)


@pytest.mark.parametrize('marker', ['HERMES_KANBAN_TASK', 'HERMES_KANBAN_WORKER_TASK'])
def test_worker_preserved(monkeypatch, marker):
    monkeypatch.setenv(marker, 't_test')
    assert plugin.terminal_request(tool_name='terminal', args={'command': 'nice'}) is None


def test_fixed_http_readback_unchanged():
    assert plugin.terminal_request(tool_name='terminal', args={'command': 'curl', 'http_readback': True}) is None


def test_inherited_higher_floor():
    result = plugin.terminal_request(tool_name='terminal', args={'command': 'nice'})
    output = subprocess.check_output(['nice', '-n', '15', 'bash', '-c', result['args']['command']], text=True)
    assert int(output) == min(19, os.getpriority(os.PRIO_PROCESS, 0) + 15)


def test_not_interactive():
    assert plugin.terminal_request(tool_name='run_in_user_terminal', args={'command': 'nice'}) is None


def test_no_false_optout():
    assert run("printf '%s' HUB_NICE_OK=1 >/dev/null; nice") == max(os.getpriority(os.PRIO_PROCESS, 0), 10)


def test_config_and_empty_override(monkeypatch, tmp_path):
    monkeypatch.delenv('HERMES_TERMINAL_NICE_HOSTS')
    (tmp_path / 'kanban-worker-nice.yaml').write_text('protected_hosts:\n  - orchestrator\n')
    assert run() == max(os.getpriority(os.PRIO_PROCESS, 0), 10)
    monkeypatch.setenv('HERMES_TERMINAL_NICE_HOSTS', '')
    assert run() == os.getpriority(os.PRIO_PROCESS, 0)


def test_idempotent_and_preserve_args():
    args = {'command': 'nice', 'background': True, 'workdir': '/example'}
    first = plugin.terminal_request(tool_name='terminal', args=args)['args']
    assert first['background'] and first['workdir'] == '/example'
    assert args['command'] == 'nice'
    assert plugin.terminal_request(tool_name='terminal', args=first) is None


@pytest.mark.parametrize('survivable_shell', [False, True])
@pytest.mark.parametrize('entry', ['terminal', 'execute_code'])
@pytest.mark.parametrize('command,optout', [('nice', False), ('HUB_NICE_OK=1 nice', True)])
def test_registered_dispatch_paths(monkeypatch, tmp_path, entry, command, optout, survivable_shell):
    """Real core dispatch and local shell; execute_code uses its generated RPC stub."""
    import json
    import time
    from types import SimpleNamespace
    model_tools = pytest.importorskip('model_tools')
    from hermes_cli import plugins
    from tools.code_execution_tool import generate_hermes_tools_module
    from tools.code_execution_rpc import _default_dispatch, _handle_rpc_request

    if survivable_shell:
        # The isolated HOME disables production's survivable-process setting.
        # Exercise its REAL tee/subshell builder without needing a systemd scope.
        from tools.environments import base
        from tools.process_registry_survivable import tee_wrapped
        wrap = base._wrap_command_script
        monkeypatch.setattr(base, '_wrap_command_script',
                            lambda *a, **kw: tee_wrapped(wrap(*a, **kw), tmp_path / 'command.log'))

    middleware = {}
    plugin.register(SimpleNamespace(register_middleware=lambda kind, fn: middleware.setdefault(kind, []).append(fn)))
    monkeypatch.setattr(plugins.get_plugin_manager(), '_middleware', middleware)
    monkeypatch.setattr(plugins.get_plugin_manager(), '_hooks', {})
    monkeypatch.setenv('TERMINAL_ENV', 'local')
    monkeypatch.setenv('TERMINAL_CWD', str(tmp_path))
    task = 'nice-test-' + entry + str(time.monotonic_ns())
    dispatch = _default_dispatch(task)
    if entry == 'terminal':
        result = dispatch('terminal', {'command': command, 'workdir': str(tmp_path)})
    else:
        scope = {}
        exec(generate_hermes_tools_module(['terminal']), scope)
        def rpc(name, args):
            return json.loads(_handle_rpc_request(
                {'tool': name, 'args': args}, allowed_tools=frozenset({'terminal'}),
                tool_call_counter=[0], max_tool_calls=1, dispatch=dispatch,
                tool_call_log=[], call_start=time.monotonic(), where='nice-test'))
        scope['_call'] = rpc
        result = scope['terminal'](command, workdir=str(tmp_path))
    result = json.loads(result) if isinstance(result, str) else result
    assert result.get('exit_code') == 0, result
    floor = os.getpriority(os.PRIO_PROCESS, 0)
    assert int(result['output'].strip()) == (floor if optout else max(floor, 10))
