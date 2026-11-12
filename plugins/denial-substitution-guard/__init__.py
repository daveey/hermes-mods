"""Bounded AWS denial mistake guard. No authority-grant path; not a sandbox."""
from __future__ import annotations

import importlib.util as _iu
import logging
import re
from pathlib import Path as _Path

logger = logging.getLogger(__name__)
_AWS_SHAPED = re.compile(r"\baws(?:cli)?\b|boto3|botocore|token-broker", re.I)
_spec = _iu.spec_from_file_location(__name__ + ".guard", _Path(__file__).with_name("guard.py"))
assert _spec is not None and _spec.loader is not None
guard = _iu.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def _pre_tool_call(tool_name="", args=None, **native_ids):
    try:
        why = guard.classify(tool_name, args, **native_ids)
    except Exception:
        logger.exception("denial-substitution-guard classify failed")
        # Recognized mutations must not turn a classifier failure into permission.
        try:
            mutating = guard.is_mutating(tool_name, args)
        except Exception:
            logger.exception("denial-substitution-guard is_mutating failed")
            # Both analyses failed: hold anything that mentions AWS, never raise.
            try:
                mutating = bool(_AWS_SHAPED.search(repr(args)[:1_000_000]))
            except Exception:
                mutating = True
        why = "DENIAL GUARD: recognized AWS mutation held after classifier failure." if mutating else None
    return {"action": "block", "message": why} if why else None


def _post_tool_call(tool_name="", args=None, result=None, **native_ids):
    try:
        guard.record_from_result(tool_name, args, result, **native_ids)
    except Exception:
        guard._UNHEALTHY[str(guard.resolve_root())] = "post-record failure"
        logger.exception("denial-substitution-guard failed to persist observation; narrow unhealthy hold latched")


def register(ctx):
    guard.resolve_root()  # capture account root once at registration
    guard._PROD = True
    ctx.register_hook("pre_tool_call", _pre_tool_call)
    ctx.register_hook("post_tool_call", _post_tool_call)
