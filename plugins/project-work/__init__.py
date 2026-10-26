"""Profile-local project-work entry point; no private defaults or target discovery."""
import importlib

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
    if backend == 'docker' and any(config.get(key) for key in (
            'docker_volumes', 'docker_extra_args', 'docker_mount_cwd_to_workspace')):
        raise PermissionError('Project work forbids host mounts and custom Docker arguments')


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
