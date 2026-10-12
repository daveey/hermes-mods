"""Show pooled Anthropic OAuth logins on the desktop/dashboard Accounts card.

Upstream bug: ``hermes_cli.web_server_oauth._anthropic_oauth_status`` only reads the legacy
``.anthropic_oauth.json`` file and env vars, but ``hermes auth add anthropic`` stores logins as
credential-pool entries in ``auth.json`` — so the card renders "signed out" even though the
agent uses those logins fine. Upstream PR: NousResearch/hermes-agent#112591.

This module is imported by the dashboard at startup (in-process plugin API). It wraps the status
function so that, ONLY when the original reports logged-out, pooled OAuth entries are counted.
Once upstream reads the pool itself, the original returns logged_in=True and this wrapper is a
no-op. Any failure leaves the original behaviour untouched.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter

_log = logging.getLogger("hermes.plugins.anthropic-pool-status")
_MARK = "_anthropic_pool_status_wrapped"
_state: Dict[str, Any] = {"applied": False, "reason": "not attempted"}


def _pooled_status(truncate: Callable[[Any], Any]) -> Optional[Dict[str, Any]]:
    from hermes_cli.auth import read_credential_pool

    pooled = [
        e for e in (read_credential_pool("anthropic") or [])
        if isinstance(e, dict) and e.get("auth_type") == "oauth" and e.get("access_token")
    ]
    if not pooled:
        return None
    pooled.sort(key=lambda e: e.get("priority", 0))
    first = pooled[0]
    labels = ", ".join(str(e.get("label") or e.get("id") or "?") for e in pooled)
    return {
        "logged_in": True,
        "source": "credential_pool",
        "source_label": f"{len(pooled)} pooled OAuth login{'s' if len(pooled) != 1 else ''} ({labels})",
        "token_preview": truncate(first.get("access_token")),
        "expires_at": first.get("expires_at_ms"),
        "has_refresh_token": bool(first.get("refresh_token")),
    }


def apply() -> Dict[str, Any]:
    """Install the wrapper (idempotent). Returns the current state dict."""
    try:
        import hermes_cli.web_server_oauth as wso
    except Exception as exc:  # module renamed/moved upstream
        _state.update(applied=False, reason=f"import failed: {exc}")
        _log.warning("anthropic-pool-status: %s — leaving Hermes untouched", _state["reason"])
        return _state

    original = getattr(wso, "_anthropic_oauth_status", None)
    catalog = getattr(wso, "_OAUTH_PROVIDER_CATALOG", None)
    truncate = getattr(wso, "_truncate_token", None)
    if not callable(original) or catalog is None or not callable(truncate):
        _state.update(applied=False, reason="expected functions not found (upstream changed)")
        _log.warning("anthropic-pool-status: %s — leaving Hermes untouched", _state["reason"])
        return _state
    if getattr(original, _MARK, False):
        _state.update(applied=True, reason="already applied")
        return _state

    def wrapped() -> Dict[str, Any]:
        status = original()
        if isinstance(status, dict) and status.get("logged_in"):
            return status  # upstream (or another source) already says connected
        try:
            return _pooled_status(truncate) or status
        except Exception:
            _log.exception("anthropic-pool-status: pool read failed; using original status")
            return status

    setattr(wrapped, _MARK, True)
    wrapped.__wrapped__ = original  # type: ignore[attr-defined]
    wso._anthropic_oauth_status = wrapped
    # The catalog captured the function object at import time; repoint the anthropic card.
    patched_cards = 0
    for entry in catalog:
        if isinstance(entry, dict) and entry.get("id") == "anthropic" and entry.get("status_fn") is original:
            entry["status_fn"] = wrapped
            patched_cards += 1
    if not patched_cards:
        _state.update(applied=False, reason="anthropic catalog card not found (upstream changed)")
        wso._anthropic_oauth_status = original
        _log.warning("anthropic-pool-status: %s — leaving Hermes untouched", _state["reason"])
        return _state
    _state.update(applied=True, reason="wrapped")
    _log.info("anthropic-pool-status: Accounts card now counts pooled Anthropic OAuth logins")
    return _state


apply()

router = APIRouter()


@router.get("/status")
async def status() -> Dict[str, Any]:
    return dict(_state)
