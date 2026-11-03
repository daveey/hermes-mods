"""F1 of the final Opus review (t_c850e0f6): a read-only gh mount does not limit what a forwarded
credential can do. The Slack-reachable sandbox must hold no host credential and keep no state."""
import importlib
import sys
import types
from typing import Any

import pytest
from conftest import PACKAGE

plugin = importlib.import_module(PACKAGE + '.__init__')


@pytest.fixture(autouse=True)
def no_core_passthrough(monkeypatch):
    # Default: a core whose config declares no terminal.env_passthrough.
    fake: Any = types.ModuleType('tools.env_passthrough')
    fake._load_config_passthrough = lambda: frozenset()
    monkeypatch.setitem(sys.modules, 'tools.env_passthrough', fake)
    return fake


@pytest.fixture
def tool_home(tmp_path, monkeypatch):
    home = tmp_path.resolve() / 'profile'
    (home / 'sandbox-bin').mkdir(parents=True)
    tool = home / 'sandbox-bin' / 'gh'
    tool.write_bytes(b'\x7fELF')
    monkeypatch.setitem(sys.modules, 'hermes_constants', types.SimpleNamespace(get_hermes_home=lambda: home))
    return home, tool


def gate(monkeypatch, **config):
    config = {'env_type': 'docker', 'container_persistent': False, **config}
    monkeypatch.setitem(sys.modules, 'tools.terminal_tool', types.SimpleNamespace(_get_env_config=lambda: config))
    return plugin.require_sandbox


def test_clean_sandbox_passes(monkeypatch):
    gate(monkeypatch)()


@pytest.mark.parametrize('forward', [['GH_TOKEN'], ['GITHUB_TOKEN'], ['GH_TOKEN', 'GITHUB_TOKEN'],
                                     ['HARMLESS_LOOKING'], ['AWS_ACCESS_KEY_ID']])
def test_refuses_any_forwarded_env(monkeypatch, forward):
    with pytest.raises(PermissionError, match='docker_forward_env'):
        gate(monkeypatch, docker_forward_env=forward)()


@pytest.mark.parametrize('persistent', [True, 'true', 1, None])
def test_refuses_persistent_container(monkeypatch, persistent):
    with pytest.raises(PermissionError, match='container_persistent'):
        gate(monkeypatch, container_persistent=persistent)()


def test_absent_persistence_key_is_core_default_true_and_refuses(monkeypatch):
    config = {'env_type': 'docker'}
    monkeypatch.setitem(sys.modules, 'tools.terminal_tool', types.SimpleNamespace(_get_env_config=lambda: config))
    with pytest.raises(PermissionError, match='container_persistent'):
        plugin.require_sandbox()


@pytest.mark.parametrize('name', ['GH_TOKEN', 'SLACK_BOT_TOKEN', 'MY_SECRET', 'DB_PASSWORD',
                                  'OPENAI_API_KEY', 'AWS_SECRET_ACCESS_KEY', 'GIT_CREDENTIAL'])
def test_refuses_credential_like_docker_env(monkeypatch, name):
    with pytest.raises(PermissionError, match='docker_env'):
        gate(monkeypatch, docker_env={name: 'x'})()


def test_allows_plain_git_identity_docker_env(monkeypatch):
    gate(monkeypatch, docker_env={'GIT_AUTHOR_NAME': 'bot', 'GIT_AUTHOR_EMAIL': 'b@x',
                                  'GIT_COMMITTER_NAME': 'bot', 'GIT_COMMITTER_EMAIL': 'b@x'})()


def test_refuses_config_env_passthrough(monkeypatch, no_core_passthrough):
    no_core_passthrough._load_config_passthrough = lambda: frozenset({'GH_TOKEN'})
    with pytest.raises(PermissionError, match='env_passthrough'):
        gate(monkeypatch)()


def test_fails_closed_on_unreadable_passthrough(monkeypatch, no_core_passthrough):
    def boom():
        raise OSError('config unreadable')
    no_core_passthrough._load_config_passthrough = boom
    with pytest.raises(PermissionError, match='env_passthrough'):
        gate(monkeypatch)()


def test_non_docker_backends_unchanged(monkeypatch):
    for backend in ('modal', 'daytona', 'vercel'):
        monkeypatch.setitem(sys.modules, 'tools.terminal_tool',
                            types.SimpleNamespace(_get_env_config=lambda b=backend: {'env_type': b}))
        plugin.require_sandbox()


def test_refuses_the_deployed_alpha_shape(monkeypatch, tool_home):
    """The exact live alpha-askeve terminal block read on 2026-11-03 must refuse at startup."""
    _, tool = tool_home
    live = dict(docker_extra_args=['--runtime=runsc'], docker_volumes=[f'{tool}:/usr/local/bin/gh:ro'],
                docker_mount_cwd_to_workspace=False, docker_forward_env=['GH_TOKEN', 'GITHUB_TOKEN'],
                container_persistent=True,
                docker_env={'GIT_AUTHOR_NAME': 'alpha-askeve-bot', 'GIT_COMMITTER_NAME': 'alpha-askeve-bot'})
    with pytest.raises(PermissionError):
        gate(monkeypatch, **live)()
    # Removing the credential forward alone is not enough: the shared persistent container refuses.
    live.update(docker_forward_env=[])
    with pytest.raises(PermissionError, match='container_persistent'):
        gate(monkeypatch, **live)()
    live.update(container_persistent=False)
    gate(monkeypatch, **live)()


def test_register_refuses_before_wiring_ingress(monkeypatch, tmp_path):
    """register() must call the gate before it touches the runtime socket or Slack handler."""
    calls = []
    monkeypatch.setattr(plugin, 'load_config', lambda home: (object(), tmp_path / 'sock', None))
    monkeypatch.setattr(plugin, 'Client', lambda *a, **k: calls.append('client'))
    (tmp_path / 'project-work.json').write_text('{}')
    monkeypatch.setitem(sys.modules, 'hermes_constants', types.SimpleNamespace(get_hermes_home=lambda: tmp_path))
    monkeypatch.setitem(sys.modules, 'tools.terminal_tool', types.SimpleNamespace(
        _get_env_config=lambda: {'env_type': 'docker', 'container_persistent': False,
                                 'docker_forward_env': ['GH_TOKEN']}))
    ctx = types.SimpleNamespace(register_platform_handler=lambda *a: calls.append('handler'))
    with pytest.raises(PermissionError):
        plugin.register(ctx)
    assert calls == []


@pytest.mark.parametrize('declared,refused', [(['THIRD_PARTY_DEPLOY_TOKEN'], True), ([], False)])
def test_real_core_config_passthrough_is_read(monkeypatch, tmp_path, declared, refused):
    """Exercise the real core loader (not a stub) against a real profile config.yaml."""
    monkeypatch.delitem(sys.modules, 'tools.env_passthrough', raising=False)
    real = pytest.importorskip('tools.env_passthrough')
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    (tmp_path / 'config.yaml').write_text('terminal:\n  env_passthrough: %s\n' % declared)
    getattr(real, '_config_passthrough').clear()
    monkeypatch.setitem(sys.modules, 'tools.env_passthrough', real)
    check = gate(monkeypatch)
    if refused:
        with pytest.raises(PermissionError, match='env_passthrough'):
            check()
    else:
        check()
