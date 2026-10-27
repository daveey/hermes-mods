"""Load deployment-owned adapters into the owning gate's registry.

Paths come only from trusted plugin configuration, never tool arguments. A bad
configuration retains an operational hold instead of silently disabling checks.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import uuid


class _RegistrationScope:
    def __init__(self, registry):
        self.registry = registry
        self.disposers = []

    def register(self, name, resolve, read):
        dispose = self.registry.register(name, resolve, read)
        self.disposers.append(dispose)
        return dispose

    def dispose(self):
        for dispose in reversed(self.disposers):
            dispose()
        self.disposers.clear()


def install(ctx, registry):
    scope = _RegistrationScope(registry)
    modules = []

    def dispose():
        scope.dispose()
        for name in modules:
            sys.modules.pop(name, None)
        modules.clear()

    try:
        paths = ctx.get_config('approval_adapters', []) if hasattr(ctx, 'get_config') else []
        if paths == []:
            return
        if not isinstance(paths, list) or len(paths) > 16:
            raise ValueError('Invalid adapter configuration')
        # Register cleanup before imports: a failed/abandoned plugin load must
        # unwind adapters as well as core hooks.
        ctx.on_unload(dispose)
        for raw in paths:
            if not isinstance(raw, str) or not Path(raw).is_absolute() or not raw.endswith('.py'):
                raise ValueError('Adapter path must be an absolute Python file')
            name = '_ask_gate_adapter_' + uuid.uuid4().hex
            spec = importlib.util.spec_from_file_location(name, raw)
            if spec is None or spec.loader is None:
                raise ValueError('Adapter module unavailable')
            module = importlib.util.module_from_spec(spec)
            # Unique names prevent discovery of another thread's partial module.
            sys.modules[name] = module
            modules.append(name)
            spec.loader.exec_module(module)
            module.register(scope)
    except (Exception, SystemExit):
        dispose()

        def unavailable(context):
            raise RuntimeError('Configured approval adapter unavailable')

        # The sentinel is removed with this plugin, including reactivation. It
        # exposes no private path or exception text to the model or UI.
        scope.register('_adapter_load_failed_' + uuid.uuid4().hex, unavailable, lambda target: None)
        if not modules:
            # on_unload may already hold dispose; duplicate cleanup is harmless.
            ctx.on_unload(dispose)
