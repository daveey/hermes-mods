"""Opus re-review t_01a1a0e8ba62a5b5: every host file core bind-mounts into the docker sandbox must be
audited, not only env routes. Driven through real core's loaders against a scratch Hermes root:

  <tmp>/root/config.yaml               (root marker)
  <tmp>/root/profiles/alpha/           HERMES_HOME (.env, auth.json, config.yaml)
  <tmp>/root/profiles/other/           another profile (.env, auth.json)

No secret values: every credential file holds a placeholder."""
import importlib
import importlib.util
import os
import subprocess
import sys
import time
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
    # No core at all (plugin-suites CI: core is private) -> skip. A run that names a core checkout
    # (scripts/test-all sets HERMES_CORE_CHECKOUT whenever it puts core on PYTHONPATH) imports plainly
    # below, so a broken or missing core module there still errors instead of skipping.
    if not os.environ.get('HERMES_CORE_CHECKOUT') and importlib.util.find_spec('hermes_constants') is None:
        pytest.skip('needs Hermes core (hermes_constants not importable, HERMES_CORE_CHECKOUT unset)')
    for name in ('hermes_constants', 'tools.env_passthrough', 'tools.credential_files', 'agent.skill_utils',
                 'agent.file_safety', 'tools.environments.docker'):
        monkeypatch.delitem(sys.modules, name, raising=False)
        # A plain import: with core named (HERMES_CORE_CHECKOUT), a missing module fails, never skips.
        monkeypatch.setitem(sys.modules, name, importlib.import_module(name))
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


def assert_core_mounts(cf, expected):
    """Precondition on core's own config route. Core before 7b1217bdee6 (the alpha-askeve runtime's)
    bind-mounts a master store listed in terminal.credential_files; core from 7b1217bdee6 on skips it
    via _is_read_denied. The gate must refuse on both, so the precondition follows the core in use."""
    mounts = core_mounts(cf)
    if hasattr(cf, '_is_read_denied'):
        assert mounts == {}
    elif isinstance(expected, dict):
        assert mounts == expected
    else:
        assert list(mounts.values()) == expected


# ---- terminal.credential_files: every form core reads, and the ones it ignores, refuse ----

def test_list_form_mounting_auth_json_refuses(monkeypatch, core):
    write_config(core.home, "  credential_files: ['auth.json']\n")
    assert_core_mounts(core.cf, {'/root/.hermes/auth.json': str((core.home / 'auth.json').resolve())})
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
    assert_core_mounts(core.cf, {'/root/.hermes/service-token': str((core.home / '.env').resolve())})
    with pytest.raises(PermissionError, match='credential_files'):
        gate(monkeypatch)()


def test_relative_traversal_to_env_refuses(monkeypatch, core):
    (core.home / 'sub').mkdir()
    write_config(core.home, "  credential_files: ['sub/../.env']\n")
    assert_core_mounts(core.cf, [str((core.home / '.env').resolve())])
    with pytest.raises(PermissionError, match='credential_files'):
        gate(monkeypatch)()


def test_root_profile_mounting_another_profiles_env_refuses(monkeypatch, core):
    monkeypatch.setenv('HERMES_HOME', str(core.root))
    getattr(core.cf, '_config_files').clear()
    write_config(core.root, "  credential_files: ['profiles/other/.env']\n")
    assert_core_mounts(core.cf, [str((core.other / '.env').resolve())])
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


# ---- review of dcef823e (neutral trace, adversarial pass): surviving findings A-F ----

def skill(root, name, frontmatter=''):
    d = root / name
    d.mkdir(parents=True)
    (d / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: test\n{frontmatter}---\nbody\n')
    return d


@pytest.mark.parametrize('frontmatter', [
    'required_environment_variables:\n  - name: PLACEHOLDER_SERVICE_TOKEN\n',
    'required_credential_files:\n  - svc-token.json\n',
    'prerequisites:\n  env_vars: [PLACEHOLDER_SERVICE_TOKEN]\n',
    'setup:\n  collect_secrets:\n    - env_var: PLACEHOLDER_SERVICE_TOKEN\n'])
def test_A_skill_that_would_register_env_or_files_refuses(monkeypatch, core, frontmatter):
    """skill_view registers these after register() (a Slack /<skill> message reaches it)."""
    skill(core.home / 'skills', 'svc', frontmatter)
    write_config(core.home, '')
    with pytest.raises(PermissionError, match='forbids skill'):
        gate(monkeypatch)()


def test_A_skill_in_create_dir_refuses(monkeypatch, core, tmp_path):
    create = tmp_path / 'created'
    skill(create, 'svc', 'required_credential_files:\n  - svc-token.json\n')
    write_config(core.home, f"skills:\n  create_dir: '{create}'\n")
    with pytest.raises(PermissionError, match='forbids skill'):
        gate(monkeypatch)()


def test_A_plain_skills_allow(monkeypatch, core, tmp_path):
    skill(core.home / 'skills', 'plain', 'metadata:\n  tags: [x]\n')
    write_config(core.home, '')
    gate(monkeypatch)()


@pytest.mark.parametrize('rel', ['.aws/credentials', '.config/gh/hosts.yml', '.ssh/id_ed25519', '.netrc',
                                 '.git-credentials', '.docker/config.json'])
def test_B_ordinary_credential_in_mounted_dir_refuses(monkeypatch, core, tmp_path, rel):
    skills = tmp_path / 'other-home'
    skill(skills, 'tool')
    (skills / rel).parent.mkdir(parents=True, exist_ok=True)
    (skills / rel).write_text('PLACEHOLDER\n')
    external_dirs(core.home, str(skills))
    with pytest.raises(PermissionError, match='credential store'):
        gate(monkeypatch)()


def test_B_mount_root_inside_credential_dir_refuses(monkeypatch, core):
    (core.user / '.aws').mkdir()
    (core.user / '.aws' / 'config').write_text('[default]\n')
    external_dirs(core.home, '~/.aws')
    with pytest.raises(PermissionError, match='credential store'):
        gate(monkeypatch)()


def test_B_git_config_with_token_url_refuses(monkeypatch, core, tmp_path):
    repo = tmp_path / 'skills-repo'
    skill(repo, 'tool')
    (repo / '.git').mkdir()
    (repo / '.git' / 'config').write_text('[remote "origin"]\n  url = https://x-access-token:PLACEHOLDER@example.com/r\n')
    external_dirs(core.home, str(repo))
    with pytest.raises(PermissionError, match='credential store'):
        gate(monkeypatch)()


def test_B_git_config_without_credentials_allows(monkeypatch, core, tmp_path):
    repo = tmp_path / 'skills-repo'
    skill(repo, 'tool')
    (repo / '.git').mkdir()
    (repo / '.git' / 'config').write_text('[remote "origin"]\n  url = https://example.com/r.git\n')
    external_dirs(core.home, str(repo))
    gate(monkeypatch)()


def test_C_twenty_thousand_files_register_quickly(monkeypatch, core, tmp_path):
    big = tmp_path / 'big'
    skill(big, 'tool')
    for i in range(20):
        d = big / 'tool' / f'd{i}'
        d.mkdir()
        for j in range(1000):
            (d / f'f{j}').touch()
    external_dirs(core.home, str(big))
    start = time.monotonic()
    gate(monkeypatch)()
    assert time.monotonic() - start < 5


@pytest.mark.skipif(os.geteuid() == 0, reason='root reads a 000 directory')
def test_D_unreadable_subdir_fails_closed(monkeypatch, core, tmp_path):
    skills = tmp_path / 'shared-skills'
    skill(skills, 'tool')
    (skills / 'tool' / 'locked').mkdir()
    os.chmod(skills / 'tool' / 'locked', 0)
    try:
        external_dirs(core.home, str(skills))
        with pytest.raises(PermissionError, match='cannot verify'):
            gate(monkeypatch)()
    finally:
        os.chmod(skills / 'tool' / 'locked', 0o755)


def test_E_hard_link_to_profile_env_refuses(monkeypatch, core, tmp_path):
    skills = tmp_path / 'shared-skills'
    skill(skills, 'tool')
    os.link(core.home / '.env', skills / 'tool' / 'notes.txt')
    external_dirs(core.home, str(skills))
    with pytest.raises(PermissionError, match='credential store'):
        gate(monkeypatch)()


def test_F_trusted_project_skills_audited_regardless_of_cwd(monkeypatch, core, tmp_path):
    repo = tmp_path / 'proj'
    skill(repo / '.hermes' / 'skills', 'x')
    (repo / '.hermes' / 'skills' / 'x' / '.env').write_text(PLACEHOLDER)
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    monkeypatch.chdir(tmp_path)  # register-time cwd outside the project
    write_config(core.home, f"skills:\n  trusted_project_dirs: ['{repo}']\n")
    with pytest.raises(PermissionError, match='credential store'):
        gate(monkeypatch)()


def test_hub_cache_env_refuses(monkeypatch, core):
    (core.home / 'skills' / '.hub' / 'pkg').mkdir(parents=True)
    (core.home / 'skills' / '.hub' / 'pkg' / '.env').write_text(PLACEHOLDER)
    write_config(core.home, '')
    with pytest.raises(PermissionError, match='credential store'):
        gate(monkeypatch)()


def test_cache_dir_contents_are_not_walked(monkeypatch, core):
    """Uploads are the agent's input: a user-sent file named .env must not stop registration."""
    (core.home / 'attachments').mkdir()
    (core.home / 'attachments' / '.env').write_text(PLACEHOLDER)
    write_config(core.home, '')
    gate(monkeypatch)()


# ---- second review (cut off by a gateway restart; its candidate findings checked here) ----

def test_cache_dir_symlinked_outside_home_refuses(monkeypatch, core, tmp_path):
    outside = tmp_path / 'code'
    outside.mkdir()
    (core.home / 'attachments').symlink_to(outside)
    write_config(core.home, '')
    with pytest.raises(PermissionError, match='a symlink or lies outside the Hermes home'):
        gate(monkeypatch)()


@pytest.mark.parametrize('where', ['.archive/svc', '.hub/quarantine/svc', 'tool/node_modules/svc'])
def test_skill_in_pruned_dir_still_audited(monkeypatch, core, where):
    """skill_view reaches these by direct path though the index walk prunes them."""
    skill(core.home / 'skills', where, 'required_environment_variables:\n  - name: PLACEHOLDER_TOKEN\n')
    write_config(core.home, '')
    with pytest.raises(PermissionError, match='forbids skill'):
        gate(monkeypatch)()


def test_flat_markdown_skill_audited(monkeypatch, core):
    (core.home / 'skills' / 'misc').mkdir(parents=True)
    (core.home / 'skills' / 'misc' / 'svc.md').write_text(
        '---\nname: svc\nrequired_credential_files:\n  - svc-token.json\n---\nbody\n')
    write_config(core.home, '')
    with pytest.raises(PermissionError, match='forbids skill'):
        gate(monkeypatch)()


def test_skills_symlink_cycle_terminates(monkeypatch, core):
    d = skill(core.home / 'skills', 'tool')
    (d / 'loop').symlink_to(core.home / 'skills')
    write_config(core.home, '')
    gate(monkeypatch)()


@pytest.mark.parametrize('url,refused', [('ssh://git@example.com/r.git', False), ('git@example.com:r.git', False),
                                         ('https://PLACEHOLDER@example.com/r', True),
                                         ('ssh://user:PLACEHOLDER@example.com/r', True)])
def test_git_remote_userinfo(monkeypatch, core, tmp_path, url, refused):
    repo = tmp_path / 'skills-repo'
    skill(repo, 'tool')
    (repo / '.git').mkdir()
    (repo / '.git' / 'config').write_text(f'[remote "origin"]\n  url = {url}\n')
    external_dirs(core.home, str(repo))
    if refused:
        with pytest.raises(PermissionError, match='credential store'):
            gate(monkeypatch)()
    else:
        gate(monkeypatch)()


@pytest.mark.parametrize('name', ['.ENV', '.Env.Local', '.npmrc', '.pypirc'])
def test_more_credential_basenames_refuse(monkeypatch, core, tmp_path, name):
    skills = tmp_path / 'shared-skills'
    d = skill(skills, 'tool')
    (d / name).write_text('PLACEHOLDER\n')
    external_dirs(core.home, str(skills))
    with pytest.raises(PermissionError, match='credential store'):
        gate(monkeypatch)()


# ---- final review of 10184504 (neutral trace, adversarial pass): surviving findings ----

@pytest.mark.parametrize('ref', ['${DEPLOY_PAT}', '${env:DEPLOY_PAT}', 'prefix-${DEPLOY_PAT}'])
def test_docker_env_value_reading_host_variable_refuses(monkeypatch, core, ref):
    """Core expands ${VAR} in config values, so a harmless docker_env name can carry a host secret."""
    write_config(core.home, f'  docker_env:\n    DEPLOY: "{ref}"\n')
    with pytest.raises(PermissionError, match='docker_env values that read host variables: DEPLOY'):
        gate(monkeypatch)()


def test_docker_env_literal_value_allows(monkeypatch, core):
    write_config(core.home, '  docker_env:\n    LANG: C.UTF-8\n')
    gate(monkeypatch)()


def test_cache_dir_symlinked_into_other_profile_refuses(monkeypatch, core):
    """Inside the Hermes root but not the dir its path names: another profile's subprocess home."""
    (core.other / 'home').mkdir()
    (core.other / 'home' / '.git-credentials').write_text('PLACEHOLDER\n')
    (core.home / 'cache').mkdir()
    (core.home / 'cache' / 'documents').symlink_to(core.other / 'home')
    write_config(core.home, '')
    with pytest.raises(PermissionError, match='a symlink or lies outside the Hermes home'):
        gate(monkeypatch)()


def test_cache_dir_hard_link_to_other_profile_env_refuses(monkeypatch, core):
    """Cache dirs are not walked; a multiply-linked store refuses wherever its other link is."""
    (core.home / 'cache' / 'documents').mkdir(parents=True)
    os.link(core.other / '.env', core.home / 'cache' / 'documents' / 'report.txt')
    write_config(core.home, '')
    with pytest.raises(PermissionError, match='hard-linked credential store'):
        gate(monkeypatch)()


def test_hard_link_to_user_ssh_key_in_skill_dir_refuses(monkeypatch, core, tmp_path):
    (core.user / '.ssh').mkdir()
    (core.user / '.ssh' / 'id_ed25519').write_text('PLACEHOLDER\n')
    skills = tmp_path / 'shared-skills'
    d = skill(skills, 'tool')
    os.link(core.user / '.ssh' / 'id_ed25519', d / 'notes.txt')
    external_dirs(core.home, str(skills))
    with pytest.raises(PermissionError, match='credential store'):
        gate(monkeypatch)()


@pytest.mark.parametrize('rel', ['.config/git/credentials', '.cache/huggingface/token', '.huggingface/token'])
def test_credential_suffix_in_mounted_dir_refuses(monkeypatch, core, tmp_path, rel):
    skills = tmp_path / 'shared-skills'
    skill(skills, 'tool')
    (skills / rel).parent.mkdir(parents=True, exist_ok=True)
    (skills / rel).write_text('PLACEHOLDER\n')
    external_dirs(core.home, str(skills))
    with pytest.raises(PermissionError, match='credential store'):
        gate(monkeypatch)()


@pytest.mark.parametrize('where,text', [
    ('.git/config', '[http "https://github.com/"]\n\textraheader = AUTHORIZATION: basic PLACEHOLDER\n'),
    ('.git/modules/sub/config', '[remote "origin"]\n\turl = https://x-access-token:PLACEHOLDER@example.com/r\n')])
def test_git_config_secret_forms_refuse(monkeypatch, core, tmp_path, where, text):
    repo = tmp_path / 'skills-repo'
    skill(repo, 'tool')
    (repo / where).parent.mkdir(parents=True, exist_ok=True)
    (repo / where).write_text(text)
    external_dirs(core.home, str(repo))
    with pytest.raises(PermissionError, match='credential store'):
        gate(monkeypatch)()


def test_skill_added_after_register_refuses_at_tool_call(monkeypatch, core):
    """register() passes; a skill that appears later (git pull, hub install) refuses the next tool call
    before any authentication or server call."""
    ingress_mod = importlib.import_module(PACKAGE + '.ingress')
    write_config(core.home, '')
    check = gate(monkeypatch)
    check()
    calls = []
    ingress = ingress_mod.Ingress(types.SimpleNamespace(), types.SimpleNamespace(call=lambda *a, **k: calls.append(a)),
                                  gate=check)
    monkeypatch.setattr(ingress, 'current', lambda: calls.append('current'))
    skill(core.home / 'skills', 'late', 'required_environment_variables:\n  - name: PLACEHOLDER_TOKEN\n')
    with pytest.raises(PermissionError, match='forbids skill'):
        ingress.tool('project_work_request', {'request': 'x'})
    assert calls == []


def test_register_wires_the_gate_into_ingress(monkeypatch, tmp_path):
    seen = {}

    class FakeIngress:
        def __init__(self, *a, gate=None, ingress_gate=None, store=None):
            seen['gate'] = gate
            seen['ingress_gate'] = ingress_gate

        middleware = None

    monkeypatch.setattr(plugin, 'require_sandbox', lambda: None)
    monkeypatch.setattr(plugin, 'load_config', lambda home: (object(), tmp_path / 'sock', None))
    monkeypatch.setattr(plugin, 'Client', lambda *a, **k: None)
    monkeypatch.setattr(plugin, 'DurableIngressQueue', lambda *a, **k: types.SimpleNamespace(start=lambda: None))
    monkeypatch.setattr(plugin, 'Ingress', FakeIngress)
    monkeypatch.setattr(plugin, 'register_tools', lambda ctx, ingress: None)
    (tmp_path / 'project-work.json').write_text('{}')
    (tmp_path / 'config.yaml').write_text('plugins:\n  enabled: [project-work]\n')  # ingress kill switch: on
    monkeypatch.setitem(sys.modules, 'hermes_constants', types.SimpleNamespace(get_hermes_home=lambda: tmp_path))
    plugin.register(types.SimpleNamespace(register_platform_handler=lambda *a: None))
    assert seen['gate'] is plugin.require_sandbox
    # The native-ingress gate is the same check, bound to the registered profile's scope.
    scoped = []
    monkeypatch.setattr(plugin, 'in_profile_scope', lambda home, fn: scoped.append((home, fn)))
    seen['ingress_gate']()
    assert scoped == [(tmp_path, plugin.require_sandbox)]
