"""F1 of the final Opus review (t_c850e0f6): a read-only gh mount does not limit what a forwarded
credential can do. The Slack-reachable sandbox must hold no host credential and keep no state."""
import importlib
import sys
import types
from typing import Any

import pytest
from conftest import PACKAGE

plugin = importlib.import_module(PACKAGE + '.__init__')
# The real reader, before the autouse fixture replaces it (absent on pre-fix b5f0878).
REAL_CORE_CREDENTIAL_FILES = getattr(plugin, '_core_credential_files', None)


def use_real_credential_reader(monkeypatch):
    if REAL_CORE_CREDENTIAL_FILES is None:
        monkeypatch.delattr(plugin, '_core_credential_files', raising=False)
    else:
        monkeypatch.setattr(plugin, '_core_credential_files', REAL_CORE_CREDENTIAL_FILES)


@pytest.fixture(autouse=True)
def no_core_passthrough(monkeypatch):
    # Default: a core whose config declares no terminal.env_passthrough and no credential files.
    fake: Any = types.ModuleType('tools.env_passthrough')
    fake._load_config_passthrough = lambda: frozenset()
    monkeypatch.setitem(sys.modules, 'tools.env_passthrough', fake)
    monkeypatch.setattr(plugin, '_core_credential_files', lambda: ([], []), raising=False)
    # Directory mounts are covered by test_sandbox_mounts.py against real core.
    monkeypatch.setattr(plugin, '_check_no_credential_dirs', lambda: None, raising=False)
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


def test_fails_closed_if_passthrough_loader_raises(monkeypatch, no_core_passthrough):
    """Defensive only: core 14a124's real loader swallows its own errors and returns an empty set
    (see test_real_core_unparseable_config_adds_no_route for what an unreadable config really does)."""
    def boom():
        raise OSError('loader failed')
    no_core_passthrough._load_config_passthrough = boom
    with pytest.raises(PermissionError, match='env_passthrough'):
        gate(monkeypatch)()


def test_refuses_declared_credential_files(monkeypatch):
    monkeypatch.setattr(plugin, '_core_credential_files', lambda: (['.env'], []))
    with pytest.raises(PermissionError, match=r'forbids terminal\.credential_files: \.env$'):
        gate(monkeypatch)()


def test_refuses_core_credential_file_mounts(monkeypatch):
    mount = {'host_path': '/x/token.json', 'container_path': '/root/.hermes/token.json'}
    monkeypatch.setattr(plugin, '_core_credential_files', lambda: ([], [mount]))
    with pytest.raises(PermissionError, match='/root/.hermes/token.json'):
        gate(monkeypatch)()


def test_fails_closed_if_credential_file_loader_raises(monkeypatch):
    use_real_credential_reader(monkeypatch)
    broken: Any = types.ModuleType('tools.credential_files')
    def boom():
        raise OSError('loader failed')
    broken.get_credential_file_mounts = boom
    monkeypatch.setitem(sys.modules, 'tools.credential_files', broken)
    with pytest.raises(PermissionError, match='cannot verify terminal.credential_files'):
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


@pytest.fixture
def real_core(monkeypatch, tmp_path):
    """Real core 14a124 env_passthrough + credential_files loaders against a scratch profile home."""
    use_real_credential_reader(monkeypatch)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    mods = {}
    for name in ('tools.env_passthrough', 'tools.credential_files'):
        monkeypatch.delitem(sys.modules, name, raising=False)
        mods[name] = pytest.importorskip(name)
        monkeypatch.setitem(sys.modules, name, mods[name])
    cf = mods['tools.credential_files']
    getattr(mods['tools.env_passthrough'], '_config_passthrough').clear()
    getattr(cf, '_config_files').clear()
    token = cf._registered_files_var.set({})
    yield tmp_path, cf
    cf._registered_files_var.reset(token)
    getattr(cf, '_config_files').clear()


def test_real_core_config_credential_file_mounts_profile_env(monkeypatch, real_core):
    """Review t_01a1a0e8ba62a5b5 finding 1: one config line mounts the profile's master .env.
    Core's config route skips the deny-list its skill route applies, so the gate must refuse it."""
    home, cf = real_core
    (home / '.env').write_text('PLACEHOLDER=not-a-secret\n')
    (home / 'config.yaml').write_text("terminal:\n  credential_files: ['.env']\n")
    # Precondition: core before 7b1217bdee6 (the alpha-askeve runtime's) bind-mounts it (the RED route);
    # later core skips it itself. The gate refuses on both, and the skill route refuses it on both.
    expected = [] if hasattr(cf, '_is_read_denied') else ['/root/.hermes/.env']
    assert [m['container_path'] for m in cf.get_credential_file_mounts()] == expected
    assert cf.register_credential_file('.env') is False
    with pytest.raises(PermissionError, match=r'forbids terminal\.credential_files: \.env$'):
        gate(monkeypatch)()


def test_real_core_declared_but_missing_credential_file_refuses(monkeypatch, real_core):
    """A declared entry refuses even while core skips it, so creating the file later cannot open the route."""
    home, cf = real_core
    (home / 'config.yaml').write_text("terminal:\n  credential_files: ['gh-hosts.yml']\n")
    assert cf.get_credential_file_mounts() == []
    with pytest.raises(PermissionError, match=r'forbids terminal\.credential_files: gh-hosts\.yml$'):
        gate(monkeypatch)()


def test_real_core_skill_registered_credential_file_refuses(monkeypatch, real_core):
    """A gate run after a skill was viewed in this context. At gateway register() the registry is
    empty; the skill route itself is covered by test_sandbox_mounts.py's frontmatter audit."""
    home, cf = real_core
    (home / 'service-token.json').write_text('{}')
    assert cf.register_credential_file('service-token.json') is True
    with pytest.raises(PermissionError, match=r'forbids credential file mounts: /root/\.hermes/service-token\.json$'):
        gate(monkeypatch)()


def test_real_core_clean_profile_allows(monkeypatch, real_core):
    home, _ = real_core
    (home / 'config.yaml').write_text("terminal:\n  backend: docker\n")
    gate(monkeypatch)()


def test_real_core_unparseable_config_adds_no_route(monkeypatch, real_core):
    """Review finding 2: core reads an unparseable config.yaml as {}; neither loader then forwards env
    nor mounts files, so allowing here is correct and no 'fails closed' claim depends on it."""
    home, cf = real_core
    (home / '.env').write_text('PLACEHOLDER=not-a-secret\n')
    (home / 'config.yaml').write_text("terminal: [credential_files: '.env'\n  env_passthrough: {\n")
    passthrough = sys.modules['tools.env_passthrough']._load_config_passthrough()
    assert passthrough == frozenset() and cf.get_credential_file_mounts() == []
    gate(monkeypatch)()


def test_core_without_env_passthrough_still_checks_credential_files(monkeypatch):
    """A core lacking tools.env_passthrough skips only that check, never the credential-file one."""
    monkeypatch.setitem(sys.modules, 'tools.env_passthrough', None)  # import raises ImportError
    monkeypatch.setattr(plugin, '_core_credential_files', lambda: (['.env'], []))
    with pytest.raises(PermissionError, match='credential_files'):
        gate(monkeypatch)()
