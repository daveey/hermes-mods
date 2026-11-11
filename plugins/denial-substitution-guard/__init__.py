"""denial-substitution-guard: refuse re-running a just-denied AWS operation under another identity or client.

``post_tool_call`` records AccessDenied results (action, resource, principal, invoking identity) in
``$HERMES_HOME/denial-ledger.jsonl``; ``pre_tool_call`` blocks the SAME action on the SAME resource from a different
identity/transport unless an independently verified grant exists. See SPEC.md. Fails open on any error.
"""
from __future__ import annotations

import importlib.util as _iu
import logging
from pathlib import Path as _Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_spec = _iu.spec_from_file_location(__name__ + ".guard", _Path(__file__).with_name("guard.py"))
assert _spec is not None and _spec.loader is not None
guard = _iu.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def _pre_tool_call(tool_name: str = "", args: Any = None, **_: Any) -> Optional[dict]:
    try:
        why = guard.classify(tool_name, args)
    except Exception:
        logger.debug("denial-substitution-guard classify failed", exc_info=True)
        return None
    if not why:
        return None
    logger.info("denial-substitution-guard blocked %s", tool_name)
    return {"action": "block", "message": why}


def _post_tool_call(tool_name: str = "", args: Any = None, result: Any = None, **_: Any) -> None:
    try:
        guard.record_from_result(tool_name, args, result)
    except Exception:
        logger.debug("denial-substitution-guard record failed", exc_info=True)
    return None


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _pre_tool_call)
    ctx.register_hook("post_tool_call", _post_tool_call)
