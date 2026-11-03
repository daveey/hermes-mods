"""Opus re-review t_01a1a0e8ba62a5b5: every host file core bind-mounts into the docker sandbox must be
audited, not only env routes. Driven through real core's loaders against a scratch Hermes root:

  <tmp>/root/config.yaml               (root marker)
  <tmp>/root/profiles/alpha/           HERMES_HOME (.env, auth.json, config.yaml)
  <tmp>/root/profiles/other/           another profile (.env, auth.json)

No secret values: every credential file holds a placeholder."""
import importlib
import os
import sys
import types

import pytest
from conftest import PACKAGE

plugin = importlib.import_module(PACKAGE + '.__init__')
REAL_CORE_CREDENTIAL_FILES = getattr(plugin, '_core_credential_files', None)
REAL_CHECK_DIRS = getattr(plugin, '_check_no_credential_dirs', None)
PLACEHOLDER = 'PLACEHOLDER=not-a-secret\n'


@pytest.fixture
def core(monkeypatch, tmp_path):
    """Real core credential_files / skill_utils / env_passthrough with fresh caches."""
    root = tmp_path / 'root'
    home = root / 'profiles' / 'alpha'
    other = root / 'profiles' / 'other'
    for d in (home, other):
        d.mkdir(parents=True)
        (d / '.env').write_text(PLACEHOLDER)
        (d / 'auth.json').write_text('{}')
    (root / 'config.yaml').write_text('{}\n')
    user = tmp_path / 'user'
    user.mkdir()
    monkeypatch.setenv('HOME', str(user))
    monkeypatch.setenv('HERMES_HOME', str(home))
    for name in ('hermes_constants', 'tools.env_passthrough', 'tools.credential_files', 'agent.skill_utils',
                 'agent.file_safety', 'tools.environments.docker'):
        monkeypatch.delitem(sys.modules, name, raising=False)
        monkeypatch.setitem(sys.modules, name, pytest.importorskip(name))
    if REAL_CORE_CREDENTIAL_FILES is None:  # pre-fix bytes have neither reader
        monkeypatch.delattr(plugin, '_core_credential_files', raising=False)
    else:
        monkeypatch.setattr(plugin, '_core_credential_files', REAL_CORE_CREDENTIAL_FILES)
    if REAL_CHECK_DIRS is None:
        monkeypatch.delattr(plugin, '_check_no_credential_dirs', raising=False)
    else:
        monkeypatch.setattr(plugin, '_check_no_credential_dirs', REAL_CHECK_DIRS)
    cf = sys.modules['tools.credential_files']
    getattr(sys.modules['tools.env_passthrough'], '_config_passthrough').clear()
    getattr(cf, '_config_files').clear()
    getattr(sys.modules['agent.skill_utils'], '_external_dirs_cache_clear')()
    token = cf._registered_files_var.set({})
    hc = sys.modules['hermes_constants']
    assert hc.get_default_hermes_root().resolve() == root.resolve()
    ns = types.SimpleNamespace(root=root, home=home, other=other, user=user, cf=cf)
    yield ns
    cf._registered_files_var.reset(token)
    getattr(cf, '_config_files').clear()
    getattr(sys.modules['agent.skill_utils'], '_external_dirs_cache_clear')()


def write_config(home, body):
    (home / 'config.yaml').write_text('terminal:\n  backend: docker\n' + body)


def gate(monkeypatch):
    config = {'env_type': 'docker', 'container_persistent': False}
    monkeypatch.setitem(sys.modules, 'tools.terminal_tool', types.SimpleNamespace(_get_env_config=lambda: config))
    return plugin.require_sandbox


def core_mounts(cf):
    return {m['container_path']: m['host_path'] for m in cf.get_credential_file_mounts()}


# ---- terminal.credential_files: every form core reads, and the ones it ignores, refuse ----

def test_list_form_mounting_auth_json_refuses(monkeypatch, core):
    write_config(core.home, "  credential_files: ['auth.json']\n")
    assert core_mounts(core.cf) == {'/root/.hermes/auth.json': str((core.home / 'auth.json').resolve())}
    with pytest.raises(PermissionError, match='credential_files'):
        gate(monkeypatch)()


def test_string_form_refuses(monkeypatch, core):
    """Core ignores a bare string today; the gate fails closed rather than trust that."""
    write_config(core.home, "  credential_files: '.env'\n")
    with pytest.raises(PermissionError, match='credential_files'):
        gate(monkeypatch)()


def test_mapping_form_refuses(monkeypatch, core):
    write_config(core.home, "  credential_files: {path: .env}\n")
    with pytest.raises(PermissionError, match='credential_files'):
        gate(monkeypatch)()


@pytest.mark.parametrize('value', ['42', '[42]', "[{path: .env}]", "['']", 'true'])
def test_unparseable_values_refuse(monkeypatch, core, value):
    write_config(core.home, f'  credential_files: {value}\n')
    with pytest.raises(PermissionError, match='credential_files'):
        gate(monkeypatch)()


def test_tilde_path_refuses(monkeypatch, core):
    os.symlink(core.root, core.user / '.hermes')
    write_config(core.home, "  credential_files: ['~/.hermes/profiles/alpha/.env']\n")
    with pytest.raises(PermissionError, match='credential_files'):
        gate(monkeypatch)()


def test_symlink_to_env_refuses(monkeypatch, core):
    """Core resolves the symlink inside HERMES_HOME and mounts the master .env's bytes under another name."""
    (core.home / 'service-token').symlink_to(core.home / '.env')
    write_config(core.home, "  credential_files: ['service-token']\n")
    assert core_mounts(core.cf) == {'/root/.hermes/service-token': str((core.home / '.env').resolve())}
    with pytest.raises(PermissionError, match='credential_files'):
        gate(monkeypatch)()


def test_relative_traversal_to_env_refuses(monkeypatch, core):
    (core.home / 'sub').mkdir()
    write_config(core.home, "  credential_files: ['sub/../.env']\n")
    assert list(core_mounts(core.cf).values()) == [str((core.home / '.env').resolve())]
    with pytest.raises(PermissionError, match='credential_files'):
        gate(monkeypatch)()


def test_root_profile_mounting_another_profiles_env_refuses(monkeypatch, core):
    monkeypatch.setenv('HERMES_HOME', str(core.root))
    getattr(core.cf, '_config_files').clear()
    write_config(core.root, "  credential_files: ['profiles/other/.env']\n")
    assert list(core_mounts(core.cf).values()) == [str((core.other / '.env').resolve())]
    with pytest.raises(PermissionError, match='credential_files'):
        gate(monkeypatch)()


def test_harmless_named_file_refuses_too(monkeypatch, core):
    """Policy: the Slack-reachable sandbox holds no host file at all via credential_files, harmless or not."""
    (core.home / 'notes.txt').write_text('hello\n')
    write_config(core.home, "  credential_files: ['notes.txt']\n")
    with pytest.raises(PermissionError, match='credential_files'):
        gate(monkeypatch)()


@pytest.mark.parametrize('body', ['', '  credential_files: []\n', '  credential_files: null\n'])
def test_no_credential_files_allows(monkeypatch, core, body):
    write_config(core.home, body)
    gate(monkeypatch)()


# ---- skills/cache directory mounts (core _RO_MOUNT_SOURCES) ----

def external_dirs(home, *dirs):
    write_config(home, 'skills:\n  external_dirs: [%s]\n' % ', '.join(f"'{d}'" for d in dirs))


def test_external_dir_equal_to_hermes_home_refuses(monkeypatch, core):
    external_dirs(core.home, '.')
    with pytest.raises(PermissionError, match='directory mount'):
        gate(monkeypatch)()


def test_external_dir_tilde_ancestor_refuses(monkeypatch, core):
    """'~' here is an ancestor of every Hermes home: refused before any walk."""
    monkeypatch.setenv('HOME', str(core.root.parent))
    external_dirs(core.home, '~')
    with pytest.raises(PermissionError, match='contains the Hermes home'):
        gate(monkeypatch)()


def test_external_dir_other_profile_refuses(monkeypatch, core):
    external_dirs(core.home, str(core.other))
    with pytest.raises(PermissionError, match='directory mount'):
        gate(monkeypatch)()


def test_external_dir_holding_project_env_refuses(monkeypatch, core, tmp_path):
    skills = tmp_path / 'repo-skills'
    (skills / 'tool').mkdir(parents=True)
    (skills / 'tool' / 'SKILL.md').write_text('---\nname: tool\n---\n')
    (skills / 'tool' / '.env').write_text(PLACEHOLDER)
    external_dirs(core.home, str(skills))
    with pytest.raises(PermissionError, match='credential store'):
        gate(monkeypatch)()


def test_external_dir_holding_other_profile_subtree_auth_refuses(monkeypatch, core):
    """A dir inside another profile is checked against that profile's store names too."""
    (core.other / 'mcp-tokens').mkdir()
    (core.other / 'mcp-tokens' / 'srv.json').write_text('{}')
    external_dirs(core.home, str(core.other / 'mcp-tokens'))
    with pytest.raises(PermissionError, match='credential store'):
        gate(monkeypatch)()


def test_symlinked_cache_dir_to_home_refuses(monkeypatch, core):
    (core.home / 'cache').mkdir()
    (core.home / 'cache' / 'documents').symlink_to(core.home)
    write_config(core.home, '')
    with pytest.raises(PermissionError, match='contains the Hermes home'):
        gate(monkeypatch)()


def test_clean_external_and_local_skills_allow(monkeypatch, core, tmp_path):
    """Negative control: ordinary skills (plus a skills-hub cache) and cache dirs still pass."""
    skills = tmp_path / 'shared-skills'
    (skills / 'tool').mkdir(parents=True)
    (skills / 'tool' / 'SKILL.md').write_text('---\nname: tool\n---\n')
    (skills / 'tool' / '.env.example').write_text('KEY=\n')
    (core.home / 'skills' / '.hub').mkdir(parents=True)
    (core.home / 'skills' / '.hub' / 'index.json').write_text('{}')
    (core.home / 'cache' / 'documents').mkdir(parents=True)
    (core.home / 'cache' / 'documents' / 'upload.pdf').write_bytes(b'%PDF')
    external_dirs(core.home, str(skills))
    gate(monkeypatch)()


def test_unknown_core_mount_source_fails_closed(monkeypatch, core):
    docker = sys.modules['tools.environments.docker']
    monkeypatch.setattr(docker, '_RO_MOUNT_SOURCES', (*docker._RO_MOUNT_SOURCES, ('get_new_mounts', False, 'new')))
    write_config(core.home, '')
    with pytest.raises(PermissionError, match='get_new_mounts'):
        gate(monkeypatch)()


def test_dir_audit_error_fails_closed(monkeypatch, core):
    def boom(*a, **k):
        raise OSError('walk failed')
    monkeypatch.setattr(core.cf, '_skill_dir_roots', boom)
    write_config(core.home, '')
    with pytest.raises(PermissionError, match='cannot verify sandbox directory mounts'):
        gate(monkeypatch)()
