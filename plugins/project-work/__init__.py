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
load_config = importlib.import_module(__package__ + '.config').load_config
_ingress = importlib.import_module(__package__ + '.ingress')
Ingress, register_tools = _ingress.Ingress, _ingress.register_tools


def register(ctx):
    from hermes_constants import get_hermes_home
    home = Path(get_hermes_home())
    if not (home / 'project-work.json').exists():
        return
    policy, endpoint, _ = load_config(home)
    # Profile-local configuration is necessary but not sufficient: prove the
    # gateway has bound this profile before authorizing each individual turn.
    ingress = Ingress(policy, Client(endpoint))
    def wire(native, adapter):
        native.use(ingress.middleware)
    ctx.register_platform_handler('slack', wire)
    register_tools(ctx, ingress)
