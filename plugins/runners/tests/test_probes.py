"""Fictional probes only: no SSH, credentials or live service access."""
import json
import os
import sys
import time

import pytest

from plugins.runners.probes import ssh, linux, macos

REF = 'service_0000000000000001'


def spec(platform='linux', **kw):
    return ssh.ProbeSpec(platform, {REF: 'example.service' if platform == 'linux' else 'gui/501/org.example.runner'}, '/build', **kw)


@pytest.mark.parametrize('value', ['x;touch /bad', '--help', 'foo*', 'foo\nbar', 'foo', '../x.service'])
def test_invalid_linux_service(value):
    with pytest.raises(ValueError):
        ssh.ProbeSpec('linux', {REF: value}, '/build')


@pytest.mark.parametrize('value', ['org.example.runner', 'gui/abc/org.example.runner', 'system/*', 'system/org.x;id', 'user/1/../bad'])
def test_invalid_macos_service(value):
    with pytest.raises(ValueError):
        ssh.ProbeSpec('macos', {REF: value}, '/build')


@pytest.mark.parametrize('kwargs', [dict(services={'wrong': 'x.service'}), dict(build_volume='/build;id'), dict(build_volume='../build'), dict(tools=('sh',)), dict(platform='windows')])
def test_rejects_injection(kwargs):
    args = dict(platform='linux', services={REF: 'example.service'}, build_volume='/build')
    args.update(kwargs)
    with pytest.raises(ValueError):
        ssh.ProbeSpec(**args)


def test_transport_is_fixed_and_isolated():
    target = ssh.SSHTarget('runner.example.invalid', 'monitor', '/keys/probe', '/keys/known_hosts')
    argv = ssh.ssh_argv(target, include_tools=True)
    assert argv[-1] == 'probe-tools-v1'
    for option in ['StrictHostKeyChecking=yes', 'IdentitiesOnly=yes', 'IdentityAgent=none', 'ConnectTimeout=5', 'ClearAllForwardings=yes', 'BatchMode=yes', 'GlobalKnownHostsFile=/dev/null', 'ControlMaster=no', 'ControlPath=none']:
        assert option in argv
    assert argv[argv.index('-F') + 1] == '/dev/null'
    assert '-T' in argv
    with pytest.raises(ValueError):
        ssh.SSHTarget('-oProxyCommand=bad', 'monitor', '/keys/probe', '/keys/known_hosts')


def test_known_hosts_cannot_expand_multiple_paths():
    with pytest.raises(ValueError):
        ssh.SSHTarget('runner.example.invalid', 'monitor', '/keys/probe', '/keys/trusted /keys/untrusted')


def test_hung_process_is_killed_and_reaped():
    start = time.monotonic()
    with pytest.raises(ssh.ProbeError, match='timeout'):
        ssh._bounded_run([sys.executable, '-c', 'import time; time.sleep(60)'], timeout=.15)
    assert time.monotonic() - start < 2


def test_stdout_and_stderr_have_combined_bound():
    for fd in (1, 2):
        with pytest.raises(ssh.ProbeError, match='output_limit'):
            ssh._bounded_run([sys.executable, '-c', f'import os; os.write({fd}, b"x" * 200000)'], max_output=1024)


def test_nonzero_output_is_not_disclosed():
    with pytest.raises(ssh.ProbeError, match='process_failed') as error:
        ssh._bounded_run([sys.executable, '-c', 'import sys; print("PRIVATE"); sys.exit(1)'])
    assert 'PRIVATE' not in str(error.value)


def test_rendered_script_rejects_arbitrary_original_command(monkeypatch):
    namespace = {'__name__': 'fixture'}
    exec(ssh.render_script(spec()), namespace)
    monkeypatch.setenv('SSH_ORIGINAL_COMMAND', 'probe-v1; id')
    with pytest.raises(SystemExit):
        namespace['main']()


@pytest.mark.parametrize('platform', ['linux', 'macos'])
def test_rendered_script_fixed_calls_and_service_state(platform, monkeypatch, capsys):
    namespace = {'__name__': 'fixture'}
    exec(ssh.render_script(spec(platform)), namespace)
    calls = []
    def fake_run(argv, **kw):
        calls.append(argv)
        return b'LoadState=loaded\nActiveState=active\n' if platform == 'linux' else b'state = running\n'
    namespace['_bounded_run'] = fake_run
    monkeypatch.setattr(namespace['os'], 'statvfs', lambda path: type('Stat', (), dict(f_bavail=10, f_frsize=4096))())
    monkeypatch.setenv('SSH_ORIGINAL_COMMAND', 'probe-v1')
    namespace['main']()
    result = json.loads(capsys.readouterr().out)
    assert result['services'][REF] == 'running'
    assert result['disk_free_bytes'] == 40960
    assert result['tools'] == {}
    assert len(calls) == 1
    assert calls[0] == (linux.service_command('example.service') if platform == 'linux' else macos.service_command('gui/501/org.example.runner'))


def test_toolchain_cache_15_minutes_and_refresh(monkeypatch):
    clock = [0]
    commands = []
    def run(argv, **kw):
        commands.append(argv[-1])
        return json.dumps(dict(version=1, services={REF: 'running'}, disk_free_bytes=100, load=[0, 0, 0], tools={'git': 'git version fixture'} if argv[-1] == 'probe-tools-v1' else {})).encode()
    monkeypatch.setattr(ssh, '_bounded_run', run)
    probe = ssh.SSHProbe(clock=lambda: clock[0])
    target = ssh.SSHTarget('runner.example.invalid', 'monitor', '/keys/probe', '/keys/known_hosts')
    config = spec(tools=('git',))
    assert probe.collect(target, config)['tools']['git'] == 'git version fixture'
    clock[0] = 899
    assert probe.collect(target, config)['tools']['git'] == 'git version fixture'
    clock[0] = 900
    probe.collect(target, config)
    probe.collect(target, config, refresh_tools=True)
    assert commands == ['probe-tools-v1', 'probe-v1', 'probe-tools-v1', 'probe-tools-v1']


def test_hung_ssh_collect_reaps_transport(monkeypatch):
    real_popen = ssh.subprocess.Popen
    processes = []
    def fake_ssh(argv, **kwargs):
        assert argv[0] == '/usr/bin/ssh'
        process = real_popen([sys.executable, '-c', 'import time; time.sleep(60)'], **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(ssh.subprocess, 'Popen', fake_ssh)
    monkeypatch.setattr(ssh, 'TOTAL_TIMEOUT', .15)
    with pytest.raises(ssh.ProbeError, match='timeout'):
        ssh.SSHProbe().collect(ssh.SSHTarget('runner.example.invalid', 'monitor', '/keys/probe', '/keys/known_hosts'), spec())
    assert processes[0].returncode == -9
    assert processes[0].stdout.closed and processes[0].stderr.closed


@pytest.mark.parametrize('adapter,text,state', [
    (linux, 'LoadState=loaded\nActiveState=inactive', 'stopped'),
    (linux, 'LoadState=loaded\nActiveState=failed', 'failed'),
    (linux, 'LoadState=not-found\nActiveState=inactive', 'missing'),
    (linux, 'ActiveState=active', 'unknown'),
    (macos, ' state = waiting\n', 'stopped'),
    (macos, ' state = not running\n', 'stopped'),
    (macos, 'pid = 42', 'unknown'),
])
def test_service_state_does_not_invent_readiness(adapter, text, state):
    assert adapter.service_state(text) == state


def test_remote_tools_bounded_and_unknown_on_failure(monkeypatch, capsys):
    namespace = {'__name__': 'fixture'}
    exec(ssh.render_script(spec(tools=('git',))), namespace)
    calls = []
    def failed(argv, **kwargs):
        assert kwargs['timeout'] <= 2
        assert kwargs['max_output'] == 8192
        calls.append(argv)
        raise namespace['ProbeError']('timeout')
    namespace['_bounded_run'] = failed
    monkeypatch.setenv('SSH_ORIGINAL_COMMAND', 'probe-tools-v1')
    monkeypatch.setattr(namespace['os'], 'statvfs', lambda path: type('Stat', (), dict(f_bavail=10, f_frsize=4096))())
    namespace['main']()
    result = json.loads(capsys.readouterr().out)
    assert result['services'] == {REF: 'unknown'}
    assert result['tools'] == {'git': None}
    assert calls[-1] == ['/usr/bin/git', '--version']


def test_invalid_response_rejected(monkeypatch):
    monkeypatch.setattr(ssh, '_bounded_run', lambda *a, **k: b'{"version":1}')
    with pytest.raises(ssh.ProbeError, match='invalid_response'):
        ssh.SSHProbe().collect(ssh.SSHTarget('runner.example.invalid', 'monitor', '/keys/probe', '/keys/known_hosts'), spec())
