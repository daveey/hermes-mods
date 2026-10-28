"""Offline supervisor acceptance; subprocesses never have credentials or scopes."""
import json
import multiprocessing as mp
import os
from pathlib import Path
import plistlib
import signal
import subprocess
import sys
import time
from threading import Event
from unittest.mock import Mock

import pytest
from plugins.runners.config import normalize
from plugins.runners.store import Store
from plugins.runners.service import Service
from plugins.runners.scripts import run_collector as runtime
from plugins.runners.scripts import install_service as installer

ROOT = Path(__file__).parents[3]
GATEWAY = '11111111-1111-4111-8111-111111111111'


def files(tmp_path):
    cfg = normalize(dict(schema_version=1, owner_gateway_id=GATEWAY, owner_profile='example'))
    config = tmp_path / 'runners.yaml'
    config.write_text(json.dumps(cfg))
    private = tmp_path / 'runtime.json'
    private.write_text(json.dumps({'credentials': {}, 'probes': {}}))
    private.chmod(0o600)
    return cfg, config, private


def test_explicit_core_root_rejects_missing_relative_and_partial_checkout(tmp_path):
    for root in ('relative', tmp_path / 'missing', tmp_path):
        with pytest.raises(RuntimeError, match='repair_core_unavailable'):
            runtime.configure_core(root)


def test_installer_forwards_explicit_core_root(tmp_path, monkeypatch):
    _, config, private = files(tmp_path)
    installs = []
    monkeypatch.setattr(installer, 'install', lambda platform, argv: installs.append(argv))
    root = tmp_path / 'core'
    assert installer.main(['--install', '--python', sys.executable, '--config', str(config),
        '--runtime', str(private), '--state-dir', str(tmp_path / 'state'), '--owner-gateway-id',
        GATEWAY, '--owner-profile', 'example', '--owner-epoch', '1', '--core-root', str(root)]) == 0
    assert installs[0][-2:] == ['--core-root', str(root)]


@pytest.mark.parametrize('field', ['config_revision', 'owner_epoch', 'owner_profile', 'invalid'])
def test_validate_every_tick_and_never_reset_fence(tmp_path, monkeypatch, field):
    cfg, config, private = files(tmp_path)
    instances = []
    class FakeCollector:
        def __init__(self, store, config, adapter, **kwargs):
            instances.append(self)
        def tick(self):
            changed = dict(cfg)
            if field == 'invalid':
                config.write_text('not: [valid')
            else:
                changed[field] = 'other' if field == 'owner_profile' else cfg[field] + 1
                config.write_text(json.dumps(changed))
    monkeypatch.setattr(runtime, 'Collector', FakeCollector)
    with pytest.raises((RuntimeError, ValueError), match='invalid_configuration|configuration_changed'):
        runtime.run(config, private, (GATEWAY, 'example', 1), tmp_path / 'state', interval=0.01, startup_delay=0)
    assert len(instances) == 1


def test_management_flag_does_not_stop_observe_only_collector(tmp_path):
    # The broker (separate OS identity) performs management; the collector never does.
    cfg, config, private = files(tmp_path)
    cfg['features']['management_operations'] = True
    config.write_text(json.dumps(cfg))
    runtime._readonly(runtime.ConfigFile(config).load())


def test_collector_has_no_management_path():
    import ast
    import pathlib
    root = pathlib.Path(runtime.__file__).resolve().parents[1]
    for path in (root / 'collector.py', root / 'scripts' / 'run_collector.py', root / 'scripts' / 'runtime_collector.py'):
        tree = ast.parse(path.read_text())
        names = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
        modules = {n.module or '' for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert not any('operations' in m or 'broker' in m for m in names | modules), path
        assert 'manage.sock' not in path.read_text(), path


@pytest.mark.parametrize('flag', ['dispatch', 'workspace_jobs'])
def test_readonly_startup(tmp_path, flag):
    cfg, config, private = files(tmp_path)
    cfg['features'][flag] = True
    config.write_text(json.dumps(cfg))
    with pytest.raises(RuntimeError, match='observe_only_required'):
        runtime.run(config, private, (GATEWAY, 'example', 1), tmp_path / 'state', once=True, startup_delay=0)
    assert not (tmp_path / 'state' / 'state.sqlite').exists()


def test_private_file_and_identity(tmp_path):
    cfg, config, private = files(tmp_path)
    private.chmod(0o644)
    with pytest.raises(RuntimeError, match='invalid_runtime_configuration'):
        runtime.load_runtime(private)
    private.chmod(0o600)
    with pytest.raises(RuntimeError, match='owner_conflict'):
        runtime.run(config, private, ('wrong', 'example', 1), tmp_path / 'state', once=True, startup_delay=0)


def test_real_dependencies_pacing_and_single_store(tmp_path, monkeypatch):
    cfg, config, private = files(tmp_path)
    factory = Mock(wraps=runtime.GitHubActions)
    stores = Mock(wraps=Store)
    monkeypatch.setattr(runtime, 'GitHubActions', factory)
    monkeypatch.setattr(runtime, 'Store', stores)
    runtime.run(config, private, (GATEWAY, 'example', 1), tmp_path / 'state', once=True, startup_delay=0)
    assert factory.call_args.kwargs['pace_requests'] is True
    assert isinstance(factory.call_args.kwargs['credential_resolver'], runtime.BrokerCredentialResolver)
    assert isinstance(factory.call_args.kwargs['transport'], runtime.GitHubHTTPTransport)
    assert stores.call_count == 1


def reader(path, cfg, queue):
    try:
        response = Service(path, cfg).inventory()
        assert response['meta']['observed_at'] is not None
        queue.put(response['meta']['owner'])
    except Exception as exc:
        queue.put(type(exc).__name__)


def test_process_singleton_readers_sigterm_and_restart(tmp_path):
    cfg, config, private = files(tmp_path)
    args = [sys.executable, str(ROOT / 'plugins/runners/scripts/run_collector.py'), '--config', str(config), '--runtime', str(private), '--state-dir', str(tmp_path / 'state'), '--owner-gateway-id', GATEWAY, '--owner-profile', 'example', '--owner-epoch', '1']
    # Test CLI startup delay is intentionally paid; no network with empty scopes.
    first = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        db = tmp_path / 'state/state.sqlite'
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            if db.exists():
                try:
                    if Service(db, cfg).inventory()['meta']['observed_at'] is not None:
                        break
                except Exception:
                    pass
            time.sleep(.05)
        assert first.poll() is None, first.communicate(timeout=2)[1]
        second = subprocess.run(args, capture_output=True, timeout=5)
        assert second.returncode == 1
        assert b'collector_running' in second.stderr
        ctx = mp.get_context('spawn'); queue = ctx.Queue()
        readers = [ctx.Process(target=reader, args=(db, cfg, queue)) for _ in range(2)]
        for p in readers: p.start()
        results = [queue.get(timeout=10) for _ in readers]
        for p in readers:
            p.join(10)
            assert p.exitcode == 0
        assert all(isinstance(r, dict) for r in results), results
        first.send_signal(signal.SIGTERM)
        assert first.wait(timeout=5) == 0
        killed = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 5
            acquired = False
            while time.monotonic() < deadline:
                try:
                    with runtime.OwnerLock(tmp_path / 'state/collector.lock', cfg, (GATEWAY, 'example', 1)):
                        pass
                except RuntimeError as exc:
                    assert str(exc) == 'collector_running'
                    acquired = True
                    break
                time.sleep(.02)
            assert acquired
        finally:
            killed.kill(); killed.wait(timeout=5)
        # SIGKILL also releases the kernel lock; persistent cache is reusable.
        restarted = subprocess.run(args + ['--once'], capture_output=True, timeout=40)
        assert restarted.returncode == 0, restarted.stderr
    finally:
        if first.poll() is None:
            first.kill(); first.wait()


def test_probe_mapping_and_tool_timestamp(tmp_path, monkeypatch):
    cfg, config, private = files(tmp_path)
    fixture = json.loads((ROOT / 'plugins/runners/contracts/fixtures.json').read_text())
    host = fixture['Host']
    host.update(management='unknown', observation_ids=[], os='linux', ssh_target_ref='ssh_0000000000000001')
    cfg['hosts'] = [host]
    config.write_text(json.dumps(cfg))
    data = {'credentials': {}, 'probes': {host['ssh_target_ref']: {
        'target': {'hostname': 'example.com', 'username': 'probe',
                   'identity_file': '/private/key', 'known_hosts_file': '/private/known_hosts'},
        'spec': {'platform': 'linux', 'services': {ref: 'example.service' for ref in host['service_refs']},
                 'build_volume': '/build', 'tools': ['git']}}}}
    private.write_text(json.dumps(data))
    probe = Mock()
    probe.collect.return_value = {'tools_age_seconds': 5, 'tools': {'git': 'git version example'}}
    monkeypatch.setattr(runtime, 'SSHProbe', lambda: probe)
    monkeypatch.setattr(runtime.time, 'time', lambda: 1000)
    class FakeCollector:
        def __init__(self, store, config, adapter, *, host_probe, job_repositories):
            assert job_repositories == {}
            self.callback = host_probe
        def tick(self):
            assert self.callback(host)['tools_observed_at'] == 995
    monkeypatch.setattr(runtime, 'Collector', FakeCollector)
    runtime.run(config, private, (GATEWAY, 'example', 1), tmp_path / 'state', once=True, startup_delay=0)
    assert probe.collect.call_count == 1
    target, spec = probe.collect.call_args.args
    assert target.hostname == 'example.com' and spec.tools == ('git',)


def test_stop_event_and_failure_release_lock(tmp_path, monkeypatch):
    cfg, config, private = files(tmp_path)
    state = tmp_path / 'state'
    tick = Mock(side_effect=RuntimeError('simulated_failure'))
    factory = Mock(return_value=Mock(tick=tick))
    monkeypatch.setattr(runtime, 'Collector', factory)
    stopped = Event(); stopped.set()
    runtime.run(config, private, (GATEWAY, 'example', 1), state, stop=stopped)
    tick.assert_not_called()
    for _ in range(2):
        with pytest.raises(RuntimeError, match='simulated_failure'):
            runtime.run(config, private, (GATEWAY, 'example', 1), state, startup_delay=0)
    assert tick.call_count == 2  # No implicit retry or retained failed Collector.
    assert factory.call_count == 3


def test_runtime_symlink_duplicate_keys_and_unknown_fields(tmp_path):
    _, _, private = files(tmp_path)
    link = tmp_path / 'link'; link.symlink_to(private)
    with pytest.raises(RuntimeError, match='invalid_runtime_configuration'):
        runtime.load_runtime(link)
    for text in ('{"credentials":{},"credentials":{},"probes":{}}',
                 '{"credentials":{},"probes":{},"owner_epoch":1}'):
        private.write_text(text)
        with pytest.raises(RuntimeError, match='invalid_runtime_configuration'):
            runtime.load_runtime(private)


def test_render_safe_units_and_explicit_actions(tmp_path):
    argv = ['/opt/example space/python', '/opt/a%/run.py', '--runtime', '/home/example/a$b.json']
    unit = installer.render_systemd(argv)
    assert 'a%%/run.py' in unit and 'a$$b.json' in unit
    assert 'Restart=on-failure' in unit
    plist = plistlib.loads(installer.render_launchd(argv))
    assert plist['ProgramArguments'] == argv
    assert plist['KeepAlive'] == {'SuccessfulExit': False}
    with pytest.raises(ValueError): installer.render_systemd(['/bin/python\nExecStart=/bad'])
    with pytest.raises(SystemExit): installer.main([])
    calls = []
    runner = lambda args, **kwargs: calls.append((args, kwargs))
    path = installer.install('linux', argv, home=tmp_path, runner=runner)
    assert path == tmp_path / '.config/systemd/user/hermes-runners-collector.service'
    assert path.read_text() == unit
    installer.uninstall('linux', home=tmp_path, runner=runner)
    assert not path.exists()
    assert all(call[1]['shell'] is False for call in calls)
