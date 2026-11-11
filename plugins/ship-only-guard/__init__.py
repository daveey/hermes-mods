"""Hermes adapter for the standalone ship-only command analyzer."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_spec = spec_from_file_location(__name__ + ".guard", Path(__file__).with_name("guard.py"))
assert _spec is not None and _spec.loader is not None
guard = module_from_spec(_spec)
_spec.loader.exec_module(guard)


def _pre_tool_call(tool_name="", args=None, **_):
    why = guard.check_tool(tool_name, args)
    return {"action": "block", "message": why} if why else None


def register(ctx):
    ctx.register_hook("pre_tool_call", _pre_tool_call)
