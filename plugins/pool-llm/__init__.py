"""pool-llm: Hermes subscription account pool for scripts/evals (bin/hermes-pool-llm) + credit-error hooks."""
from __future__ import annotations

if __package__:
    from . import hooks
else:  # pytest imports a hyphenated plugin dir without a parent package
    import importlib.util
    import os
    import sys
    _key = "pool_llm_plugin_hooks"
    _spec = importlib.util.spec_from_file_location(_key, os.path.join(os.path.dirname(__file__), "hooks.py"))
    hooks = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(hooks)
    hooks = sys.modules.setdefault(_key, hooks)


def register(ctx) -> None:
    hooks.register(ctx)
