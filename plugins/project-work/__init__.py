"""Profile-local project-work entry point; no private defaults or target discovery."""
import importlib
import os
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
    try:
        from tools.env_passthrough import _load_config_passthrough
    except ImportError:  # core without config passthrough cannot forward it
        return
    try:
        passthrough = sorted(_load_config_passthrough())
    except Exception as exc:  # fail closed: unreadable passthrough config
        raise PermissionError('Project work cannot verify terminal.env_passthrough') from exc
    if passthrough:
        raise PermissionError('Project work forbids terminal.env_passthrough: ' + ', '.join(passthrough))


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
    ingress = Ingress(policy, client, queue)
    def wire(native, adapter):
        native.use(ingress.middleware)
    ctx.register_platform_handler('slack', wire)
    register_tools(ctx, ingress)
    queue.start()
