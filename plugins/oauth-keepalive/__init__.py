"""Keep every pooled OAuth login's access token fresh, even for accounts nobody is using.

Why: the credential pool only refreshes a token when it *selects* that entry, and the
account-usage fetch (status-bar chips / model picker) is read-only by design. Idle pooled
accounts therefore sit on expired access tokens (~8h lifetime), the usage endpoint rejects
them, and the chips render every idle account as grey "unknown" — looks like no capacity.

What: a daemon thread per long-lived Hermes process wakes every CHECK_EVERY_S and, for each
OAuth entry in the watched pools whose access token expires within SKEW_S (or already has),
calls the public ``CredentialPool.try_refresh_matching(credential_id=...)`` — the exact path
``hermes auth refresh <provider> <n>`` uses, which takes the cross-process auth-store lock and
re-syncs from disk first, so several processes running this never double-spend a rotating
refresh token (the loser adopts the winner's tokens).

Safety: first check is delayed FIRST_CHECK_S so short-lived CLI invocations exit before acting;
any failure is logged and swallowed; never touches non-OAuth entries.
"""
from __future__ import annotations

import logging
import threading
import time

_log = logging.getLogger("hermes.plugins.oauth-keepalive")

PROVIDERS = ("anthropic", "openai-codex")
FIRST_CHECK_S = 90
CHECK_EVERY_S = 15 * 60
SKEW_S = 45 * 60  # refresh anything expiring within the next 45 min

_started = False
_start_lock = threading.Lock()
_stop = threading.Event()


def _expires_at(provider: str, entry) -> float | None:
    """Epoch seconds the entry's access token expires, or None if unknown."""
    ms = getattr(entry, "expires_at_ms", None)
    if isinstance(ms, (int, float)) and ms > 0:
        return float(ms) / 1000.0
    if provider == "openai-codex":
        try:
            from hermes_cli.auth_codex import _decode_jwt_claims

            exp = _decode_jwt_claims(getattr(entry, "access_token", None)).get("exp")
            if isinstance(exp, (int, float)):
                return float(exp)
        except Exception:
            pass
    return None


def check_once() -> list[str]:
    """Refresh due entries across watched pools. Returns human-readable result lines."""
    from agent.credential_pool import load_pool

    results: list[str] = []
    now = time.time()
    for provider in PROVIDERS:
        try:
            pool = load_pool(provider)
            entries = list(pool.entries())
        except Exception as exc:
            results.append(f"{provider}: pool unreadable ({exc})")
            continue
        for entry in entries:
            if getattr(entry, "auth_type", None) != "oauth" or not getattr(entry, "refresh_token", None):
                continue
            if getattr(entry, "last_status", None) == "dead":
                continue
            exp = _expires_at(provider, entry)
            if exp is None or exp - now > SKEW_S:
                continue
            label = getattr(entry, "label", None) or entry.id
            try:
                refreshed = pool.try_refresh_matching(credential_id=entry.id)
                if refreshed is None:
                    msg = f"{provider}/{label}: refresh FAILED (re-login may be needed: hermes auth add {provider})"
                    _log.warning("oauth-keepalive: %s", msg)
                else:
                    msg = f"{provider}/{label}: refreshed (was {'expired' if exp <= now else 'expiring'})"
                    _log.info("oauth-keepalive: %s", msg)
                results.append(msg)
            except Exception as exc:
                msg = f"{provider}/{label}: refresh error {exc!r}"
                _log.warning("oauth-keepalive: %s", msg)
                results.append(msg)
    return results


def _loop() -> None:
    if _stop.wait(FIRST_CHECK_S):
        return
    while not _stop.is_set():
        try:
            check_once()
        except Exception:
            _log.exception("oauth-keepalive: check failed")
        if _stop.wait(CHECK_EVERY_S):
            return


def register(ctx):
    global _started
    with _start_lock:
        if _started:
            return None
        _started = True
    _stop.clear()
    threading.Thread(target=_loop, name="hermes-oauth-keepalive", daemon=True).start()
    on_unload = getattr(ctx, "on_unload", None)
    if callable(on_unload):
        try:
            on_unload(_stop.set)
        except Exception:
            pass
    return None
