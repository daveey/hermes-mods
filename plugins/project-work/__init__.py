"""Profile-local project-work entry point; no private defaults or target discovery."""
import importlib
import os
import re
import stat

from pathlib import Path
import sys
import types

# pytest and some disk-plugin loaders import a hyphenated folder as __init__.
if not __package__:
    package = types.ModuleType('_project_work_plugin')
    package.__path__ = [str(Path(__file__).resolve().parent)]
    sys.modules.setdefault(package.__name__, package)
    __package__ = package.__name__

Client = importlib.import_module(__package__ + '.client').Client
DurableIngressQueue = importlib.import_module(__package__ + '.client').DurableIngressQueue
load_config = importlib.import_module(__package__ + '.config').load_config
_ingress = importlib.import_module(__package__ + '.ingress')
Ingress, register_tools = _ingress.Ingress, _ingress.register_tools


def require_sandbox():
    # execute_code uses this same terminal backend. Local/SSH/custom backends
    # cannot establish that host sockets are inaccessible; fail closed.
    from tools.terminal_tool import _get_env_config
    config = _get_env_config()
    backend = config.get('env_type')
    if backend not in {'docker', 'modal', 'daytona', 'vercel'}:
        raise PermissionError('Project work requires a sandboxed terminal/code backend')
    if backend != 'docker':
        return
    # A read-only tool mount says nothing about what a credential can do. The Slack-reachable
    # sandbox must hold no host credential at all, and must not keep state across sessions.
    _check_no_credentials(config)
    # Core defaults container_persistent to true, so an absent key is persistent too.
    if config.get('container_persistent', True) is not False:
        raise PermissionError('Project work requires container_persistent: false (no state shared across sessions)')
    if config.get('docker_mount_cwd_to_workspace'):
        raise PermissionError('Project work forbids mounting the host cwd into the sandbox')
    extra = list(config.get('docker_extra_args') or [])
    if extra not in _ALLOWED_DOCKER_ARGS:
        raise PermissionError('Project work forbids custom Docker arguments other than --runtime=runsc')
    volumes = config.get('docker_volumes') or []
    if volumes:
        try:
            from hermes_constants import get_hermes_home
            tool_dir = os.path.realpath(os.path.join(str(get_hermes_home()), 'sandbox-bin'))
        except Exception as exc:  # fail closed: no profile home, no mounts
            raise PermissionError('Project work cannot verify host mounts without a profile home') from exc
        for volume in volumes:
            _check_tool_mount(volume, tool_dir)


# gVisor only tightens the default runc sandbox; every other argument refuses.
_ALLOWED_DOCKER_ARGS = ([], ['--runtime=runsc'], ['--runtime', 'runsc'])

# Static docker_env values are visible config, but a name like these means a secret is being planted.
_SECRET_NAME_HINTS = ('TOKEN', 'SECRET', 'PASSWORD', 'PASSWD', 'CREDENTIAL', 'PRIVATE_KEY', 'API_KEY',
                      'ACCESS_KEY', 'BEARER', 'COOKIE')


def _check_no_credentials(config):
    """Refuse every route by which a host credential reaches the Slack-reachable sandbox.

    docker_forward_env is an operator opt-in that core forwards past its provider-credential
    blocklist on every exec, so any entry at all is refused (its value cannot be audited here).
    terminal.env_passthrough forwards host values the same way, minus provider keys.
    terminal.credential_files bind-mounts profile files (core's config route before 7b1217bdee6
    even mounts the master .env, which its skill route refuses), so any declaration or core mount
    is refused, whichever core is running.
    """
    forward = config.get('docker_forward_env') or []
    if forward:
        raise PermissionError('Project work forbids docker_forward_env (host credentials in the sandbox): '
                              + ', '.join(sorted(map(str, forward))))
    env = config.get('docker_env') or {}
    if not isinstance(env, dict):
        raise PermissionError('Project work cannot verify docker_env')
    secret = sorted(k for k in env if any(h in str(k).upper() for h in _SECRET_NAME_HINTS))
    if secret:
        raise PermissionError('Project work forbids credential-like docker_env names: ' + ', '.join(secret))
    _check_docker_env_refs()
    _check_no_env_passthrough()
    try:
        declared, mounts = _core_credential_files()
    except Exception as exc:  # fail closed: the mount route cannot be audited
        raise PermissionError('Project work cannot verify terminal.credential_files') from exc
    if declared:
        raise PermissionError('Project work forbids terminal.credential_files: '
                              + ', '.join(sorted(map(str, declared))))
    if mounts:
        raise PermissionError('Project work forbids credential file mounts: '
                              + ', '.join(sorted(str(m.get('container_path')) for m in mounts)))
    _check_no_credential_dirs()


def _check_docker_env_refs():
    """Refuse a terminal.docker_env value that names a host variable. Core expands ``${VAR}`` /
    ``${env:VAR}`` in config values before the backend sees them, so ``{DEPLOY: "${DEPLOY_PAT}"}``
    forwards a host secret under a harmless name, as docker_forward_env would. The expanded value
    the backend gets cannot show this, so the raw config.yaml is read."""
    try:
        from hermes_cli.config import cfg_get, read_raw_config
        raw = cfg_get(read_raw_config(), 'terminal', 'docker_env')
    except Exception as exc:  # fail closed
        raise PermissionError('Project work cannot verify docker_env') from exc
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise PermissionError('Project work cannot verify docker_env')
    refs = sorted(str(k) for k, v in raw.items() if '${' in str(v))
    if refs:
        raise PermissionError('Project work forbids docker_env values that read host variables: ' + ', '.join(refs))


def _check_no_env_passthrough():
    try:
        from tools.env_passthrough import _load_config_passthrough
    except ImportError:  # core without config passthrough cannot forward it
        return
    try:
        passthrough = sorted(_load_config_passthrough())
    except Exception as exc:  # defensive: core's loader returns empty on its own errors
        raise PermissionError('Project work cannot verify terminal.env_passthrough') from exc
    if passthrough:
        raise PermissionError('Project work forbids terminal.env_passthrough: ' + ', '.join(passthrough))


def _core_credential_files():
    """(config declaration, mounts core would bind) for terminal.credential_files, read through core."""
    from hermes_cli.config import cfg_get, read_raw_config
    declared = cfg_get(read_raw_config(), 'terminal', 'credential_files')
    if declared is not None and not isinstance(declared, list):
        declared = [declared]
    mounts = importlib.import_module('tools.credential_files').get_credential_file_mounts()
    return list(declared or []), list(mounts)


# Directory mounts core adds to every non-capsule docker sandbox (tools/environments/docker.py
# _RO_MOUNT_SOURCES), each read here through core's side-effect-free root enumerator. A source core
# adds later refuses until it is audited here.
_MAX_WALK_FILES = 100_000
# Ordinary (non-Hermes) credential files that core's read deny-list does not cover.
_CREDENTIAL_BASENAMES = frozenset(('.netrc', '_netrc', '.git-credentials', '.pgpass', '.npmrc', '.pypirc', '.boto',
                                   '.s3cfg', '.htpasswd', 'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519'))
# Ordinary credential files known by their trailing path, wherever they sit (an XDG home in a repo).
_CREDENTIAL_SUFFIXES = ('.config/git/credentials', '.cache/huggingface/token', '.huggingface/token')
# A token in a git config: any userinfo on http(s) (https://TOKEN@host), a password on any scheme
# (ssh://git@host carries no secret), or an http.extraheader (actions/checkout stores its token there).
_URL_SECRET = re.compile(r'https?://[^/\s@]+@|[a-z][a-z0-9+.-]*://[^/\s@:]+:[^/\s@]+@'
                         r'|^\s*extraheader\s*=', re.IGNORECASE | re.MULTILINE)


def _check_no_credential_dirs():
    """Refuse a skills/cache directory mount that holds, or is an ancestor of, a credential store, and
    any skill that would register an env passthrough or credential file when it is viewed.

    skills.external_dirs accepts any host path ('~', absolute, relative to HERMES_HOME), and core
    bind-mounts each one read-only, so `external_dirs: ['.']` would hand the sandbox the profile's
    .env and auth.json without touching terminal.credential_files. A skill's required_* frontmatter
    is registered at skill_view (a Slack /<skill> message reaches it), after register() has passed.
    """
    try:
        homes = _hermes_homes()
        stores, linked = _store_inodes(homes)
        if linked:
            # A second hard link holds the same bytes under any name, anywhere on the filesystem
            # (an unwalked cache dir included), so a multiply-linked store refuses outright.
            raise PermissionError('Project work forbids a hard-linked credential store: ' + ', '.join(linked))
        stores |= _user_credential_inodes()
        skill_roots, cache_roots = _core_dir_mount_roots()
        for root in skill_roots:
            _check_dir_mount(Path(root), homes, stores, walk=True)
        from hermes_constants import get_default_hermes_root, get_hermes_home
        own = [Path(os.path.abspath(get_hermes_home())), Path(os.path.abspath(get_default_hermes_root()))]
        for root in cache_roots:
            # Fixed HERMES_HOME subdirs holding what users hand the agent (uploads, pastes): their
            # contents are the agent's input, so they are not walked. That holds only while the dir is
            # what its path says: a symlink anywhere below the Hermes home (to ~/code, to another
            # profile's home/ subtree) makes it some other directory, and it refuses.
            lexical = Path(os.path.abspath(root))
            _check_dir_mount(Path(root), homes, stores, walk=False)
            if not any(lexical.is_relative_to(b) and lexical.resolve() == b.resolve() / lexical.relative_to(b)
                       for b in own):
                raise PermissionError(f'Project work forbids sandbox cache mount {root}: it is reached through '
                                      'a symlink or lies outside the Hermes home')
        create_dir = importlib.import_module('agent.skill_utils').get_skill_create_dir()
        # skill_view also searches skills.create_dir, which core does not mount.
        extra = [create_dir] if create_dir is not None and create_dir.is_dir() else []
        _check_skill_frontmatter(skill_roots + extra)
    except PermissionError:
        raise
    except Exception as exc:  # fail closed: the directory mounts cannot be audited
        raise PermissionError('Project work cannot verify sandbox directory mounts') from exc


def _core_dir_mount_roots():
    """(skill roots, cache roots) core would mount, plus every trusted project's skill dirs.

    Core picks project skill dirs from the session cwd at container creation; register() cannot know
    that cwd, so every root in skills.trusted_project_dirs is audited instead.
    """
    docker = importlib.import_module('tools.environments.docker')
    cf = importlib.import_module('tools.credential_files')
    su = importlib.import_module('agent.skill_utils')
    skills, caches = [], []
    for getter, *_ in docker._RO_MOUNT_SOURCES:
        if getter == 'get_credential_file_mounts':
            continue  # audited by _core_credential_files
        if getter == 'get_skills_directory_mount':
            skills += [d for d, _ in cf._skill_dir_roots('/root/.hermes')]
        elif getter == 'get_cache_directory_mounts':
            caches += [d for d, _ in cf._cache_dir_roots('/root/.hermes', create_missing=False)]
        else:
            raise PermissionError(f'Project work cannot verify core sandbox mount source {getter}')
    if su._skills_cfg_get('project_discovery') is not False:
        for project in sorted(su._project_trusted_dirs_from_config()):
            skills += su._candidate_project_skills_dirs(project)
    return list(dict.fromkeys(Path(d) for d in skills)), [Path(d) for d in caches]


def _hermes_homes():
    """Every Hermes home on this host, resolved: the active profile, the root and each profile dir."""
    from hermes_constants import get_default_hermes_root, get_hermes_home
    root = Path(get_default_hermes_root()).resolve()
    homes = {Path(get_hermes_home()).resolve(), root}
    profiles = root / 'profiles'
    if profiles.is_dir():
        homes.update(p.resolve() for p in profiles.iterdir() if p.is_dir())
    return homes


def _store_inodes(homes):
    """((st_dev, st_ino) of every Hermes credential store, [stores with more than one hard link]).
    The inode set catches a hard link under another name inside a walked mount."""
    from agent.file_safety import _CREDENTIAL_FILE_NAMES, _READ_DENIED_DIRS
    found, linked = set(), []
    for home in homes:
        paths = [home / n for n in _CREDENTIAL_FILE_NAMES]
        for d, *_ in _READ_DENIED_DIRS:
            if (home / d).is_dir() and not (home / d).is_symlink():
                paths += [Path(p) / f for p, _, fs in os.walk(home / d) for f in fs]
        for p in paths:
            try:
                st = os.stat(p)
            except OSError:
                continue
            found.add((st.st_dev, st.st_ino))
            if stat.S_ISREG(st.st_mode) and st.st_nlink > 1:
                linked.append(str(p))
    return found, sorted(linked)


def _user_credential_inodes():
    """(st_dev, st_ino) of the OS user's own credential files (~/.ssh/id_*, ~/.aws/credentials,
    ~/.netrc, ...): the files directly in each home credential dir plus the ordinary basenames.
    A hard link to one under a harmless name inside a walked mount is then caught."""
    import pwd
    from agent.file_safety import HOME_CREDENTIAL_DIRS
    found = set()
    for home in {Path(pwd.getpwuid(os.getuid()).pw_dir), Path(os.path.expanduser('~'))}:
        paths = [home / n for n in _CREDENTIAL_BASENAMES] + [home / s for s in _CREDENTIAL_SUFFIXES]
        for d in HOME_CREDENTIAL_DIRS:
            try:
                paths += list((home / d).iterdir())
            except OSError:
                continue
        for p in paths:
            try:
                st = os.stat(p)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                found.add((st.st_dev, st.st_ino))
    return found


def _walk_error(err):
    raise PermissionError(f'Project work cannot verify sandbox directory mount: {err}')


def _check_dir_mount(root, homes, stores, *, walk):
    real = root.resolve()
    for home in homes:
        if home == real or home.is_relative_to(real):
            raise PermissionError(f'Project work forbids sandbox directory mount {root}: it contains the '
                                  f'Hermes home {home}')
    from agent.file_safety import (_BLOCKED_PROJECT_ENV_BASENAMES, _CREDENTIAL_FILE_NAMES, _READ_DENIED_DIRS,
                                   HOME_CREDENTIAL_DIRS)
    rules = (homes, {Path(n).as_posix() for n in _CREDENTIAL_FILE_NAMES}, {d for d, *_ in _READ_DENIED_DIRS},
             [tuple(d.split('/')) for d in HOME_CREDENTIAL_DIRS], set(_BLOCKED_PROJECT_ENV_BASENAMES))
    if _credential_reason(real, rules, is_dir=True):
        raise PermissionError(f'Project work forbids sandbox directory mount {root}: it is a credential store')
    if not walk:
        return
    seen = 0
    # Core bind-mounts a symlink-free dir as-is (every file, no pruning) and otherwise mounts a copy of
    # its regular files, so every regular non-symlink file is what the sandbox can read. An unreadable
    # subdirectory refuses: the container's root may still read it.
    for dirpath, _dirs, files in os.walk(real, onerror=_walk_error):
        for name in files:
            seen += 1
            if seen > _MAX_WALK_FILES:
                raise PermissionError(f'Project work cannot verify sandbox directory mount {root}: too many files')
            path = Path(dirpath) / name
            st = os.lstat(path)
            if not stat.S_ISREG(st.st_mode):
                continue
            if (st.st_nlink > 1 and (st.st_dev, st.st_ino) in stores) or _credential_reason(path, rules):
                raise PermissionError(f'Project work forbids sandbox directory mount {root}: it holds the '
                                      f'credential store {path}')


def _credential_reason(path, rules, *, is_dir=False):
    homes, names, denied_dirs, home_cred_dirs, env_names = rules
    for home in homes:  # core's deny-list only knows the active home and the root; check every profile
        if path.is_relative_to(home):
            rel = path.relative_to(home).as_posix()
            if rel in names or rel.split('/', 1)[0] in denied_dirs:
                return True
    parts = path.parts
    # ~/.ssh, ~/.aws, ~/.config/gh, ... wherever they sit (another profile's subprocess home, a repo).
    for seq in home_cred_dirs:
        if any(parts[i:i + len(seq)] == seq for i in range(len(parts) - len(seq) + 1)):
            return True
    if is_dir:
        return False
    name = path.name.lower()  # core's deny-list matches basenames case-insensitively
    if name in env_names or name in _CREDENTIAL_BASENAMES:
        return True
    if any(parts[-len(s.split('/')):] == tuple(s.split('/')) for s in _CREDENTIAL_SUFFIXES):
        return True
    # Any git config: the repo's own, a submodule's (.git/modules/<m>/config), a worktree's.
    if path.name == 'config' and '.git' in parts[:-1]:
        try:
            return bool(_URL_SECRET.search(path.read_text(errors='replace')))
        except OSError as exc:
            raise PermissionError(f'Project work cannot verify {path}') from exc
    return False


def _check_skill_frontmatter(skill_roots):
    """Refuse any skill whose frontmatter would register an env passthrough or a credential file.

    skill_view loads more than the index walk lists: a direct path (`.archive/x`, a `.hub` copy,
    node_modules) and any flat `<name>.md` under a root. So every .md file under every root is read,
    nothing pruned, symlinked dirs followed once.
    """
    from agent.skill_utils import parse_frontmatter
    from tools.skills_tool_setup import _get_required_environment_variables
    for root in skill_roots:
        for skill_md in _iter_markdown(Path(root)):
            text = skill_md.read_text(encoding='utf-8', errors='replace')
            if not text.lstrip('\ufeff').startswith('---'):
                continue
            frontmatter, _ = parse_frontmatter(text)
            env = [e['name'] for e in _get_required_environment_variables(frontmatter or {})]
            files = (frontmatter or {}).get('required_credential_files')
            if env or files:
                raise PermissionError(f'Project work forbids skill {skill_md}: it would forward '
                                      + ', '.join(env + ([f'credential files {files}'] if files else [])))


def _check_tool_mount(volume, tool_dir):
    """Allow only a read-only mount of one regular file from <home>/sandbox-bin into /usr/local/bin."""
    refuse = PermissionError(f'Project work forbids host mount {volume!r}: only '
                             '<profile>/sandbox-bin/<file>:/usr/local/bin/<name>:ro is allowed')
    if not isinstance(volume, str):
        raise refuse
    parts = volume.split(':')
    if len(parts) != 3 or parts[2] != 'ro':
        raise refuse
    src, dst = parts[0], parts[1]
    # Relative, '..', or a symlink anywhere in the path (including sandbox-bin itself) refuses.
    if not os.path.isabs(src) or os.path.normpath(src) != src or os.path.realpath(src) != src:
        raise refuse
    if os.path.dirname(src) != tool_dir:
        raise refuse
    try:
        st = os.lstat(src)
    except OSError:
        raise refuse from None
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
        raise refuse
    if os.path.normpath(dst) != dst or os.path.dirname(dst) != '/usr/local/bin':
        raise refuse


def in_profile_scope(home, fn):
    """Call ``fn`` with ``home`` bound as the Hermes home and its terminal policy freshly read.

    Uses core's per-profile seams (``hermes_constants.set_hermes_home_override`` and
    ``tools.terminal_scope.build_profile_terminal_scope``/``set_terminal_scope``), the same pair
    ``gateway.run._profile_runtime_scope`` installs for a turn, minus the secret scope the gate
    never reads. Fails closed: a core without them, or a policy that cannot be read, refuses.
    """
    try:
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override
        from tools.terminal_scope import build_profile_terminal_scope, reset_terminal_scope, set_terminal_scope
    except ImportError as exc:
        raise PermissionError('Project work cannot bind its profile scope on this Hermes core') from exc
    home_token = set_hermes_home_override(str(home))
    try:
        try:
            # Under the home override: the managed-scope overlay is read for the current home.
            policy = build_profile_terminal_scope(Path(home))
        except Exception as exc:
            raise PermissionError('Project work cannot read its profile terminal policy') from exc
        scope_token = set_terminal_scope(policy)
        try:
            return fn()
        finally:
            reset_terminal_scope(scope_token)
    finally:
        reset_hermes_home_override(home_token)


def register(ctx):
    from hermes_constants import get_hermes_home
    home = Path(get_hermes_home())
    if not (home / 'project-work.json').exists():
        return
    policy, endpoint, _ = load_config(home)
    require_sandbox()
    # Profile-local configuration is necessary but not sufficient: prove the
    # gateway has bound this profile before authorizing each individual turn.
    client = Client(endpoint)
    queue = DurableIngressQueue(endpoint.parent / 'ingress.sqlite3', client)
    # Tool calls run inside core's per-turn profile scope, rebuilt for every turn. The native Slack
    # middleware does not: it only inherits the ContextVars the Socket Mode task was created with
    # at connect(), a snapshot that never sees later config edits. Re-read THIS profile's policy.
    ingress = Ingress(policy, client, queue, gate=require_sandbox,
                      ingress_gate=lambda: in_profile_scope(home, require_sandbox))
    def wire(native, adapter):
        native.use(ingress.middleware)
    ctx.register_platform_handler('slack', wire)
    register_tools(ctx, ingress)
    queue.start()


def _iter_markdown(root):
    seen = set()
    for dirpath, dirs, files in os.walk(root, followlinks=True, onerror=_walk_error):
        real = os.path.realpath(dirpath)
        if real in seen:  # a symlink cycle or a second link to the same tree
            dirs[:] = []
            continue
        seen.add(real)
        for name in files:
            if name.lower().endswith('.md'):
                yield Path(dirpath) / name
