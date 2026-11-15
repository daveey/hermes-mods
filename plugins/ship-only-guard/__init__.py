"""Hermes adapter for the standalone ship-only command analyzer."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import logging
import time

_spec = spec_from_file_location(__name__ + ".guard", Path(__file__).with_name("guard.py"))
assert _spec is not None and _spec.loader is not None
guard = module_from_spec(_spec)
_spec.loader.exec_module(guard)

_bspec = spec_from_file_location(__name__ + ".brief", Path(__file__).with_name("brief.py"))
assert _bspec is not None and _bspec.loader is not None
brief = module_from_spec(_bspec)
_bspec.loader.exec_module(brief)


def _pre_tool_call(tool_name="", args=None, **_):
    try:
        why = brief.check(tool_name, args)
    except Exception:  # a lint must never break the tool call it inspects
        logging.getLogger(__name__).exception("ship-only-guard brief check failed")
        why = None
    if why:
        return {"action": "block", "message": why}
    started = time.monotonic()
    why = guard.bounded_preflight(guard.check_tool, tool_name, args)
    elapsed = time.monotonic() - started
    if elapsed >= 1:
        logging.getLogger(__name__).info("ship-only-guard preflight completed in %.3fs (%s)",
                                         elapsed, "refused" if why else "allowed")
    return {"action": "block", "message": why} if why else None


# Leave cleanup/return headroom after the aggregate subprocess deadline;
# the host's fail-closed 30-second timeout remains the hard backstop.
_pre_tool_call.hermes_hook_timeout = 30.0


def register(ctx):
    ctx.register_hook("pre_tool_call", _pre_tool_call)
