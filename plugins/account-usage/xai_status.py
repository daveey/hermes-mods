"""Account-usage fetcher for ``xai-oauth`` (xAI Grok sign-in) so the Account Usage chips can show it.

xAI publishes no subscription-usage API: the X Subscription pool (Grok chat/Imagine/Bot/Cursor) is
not readable over the API, and API calls are billed separately from that pool. What the OAuth token
CAN read is ``GET /v1/me``: ``team_blocked`` is true when the API team has no credits / spending
limit (every inference call then returns 402 ``personal-team-blocked:spending-limit``).

So the snapshot has one ``API access`` window: 0% = usable, 100% = blocked. That is a status, not a
quota gauge, and the window's ``detail`` says so. ``/v1/me`` is free (no tokens) and read-only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

PROVIDER = "xai-oauth"
ME_URL = "https://api.x.ai/v1/me"


def _token(api_key: Optional[str]) -> str:
    if api_key:
        return str(api_key).strip()
    from hermes_cli.auth import resolve_xai_oauth_runtime_credentials
    creds = resolve_xai_oauth_runtime_credentials(refresh_if_expiring=False)
    return str(creds.get("api_key") or "").strip()


def snapshot_from_me(me: dict):
    """Pure: ``/v1/me`` JSON -> AccountUsageSnapshot (exported for tests)."""
    from agent.account_usage import AccountUsageSnapshot, AccountUsageWindow
    blocked = bool(me.get("team_blocked"))
    window = AccountUsageWindow(
        label="API access", used_percent=100.0 if blocked else 0.0, reset_at=None,
        detail="blocked: no API credits / spending limit" if blocked else "API access ok (billed per token)")
    return AccountUsageSnapshot(
        provider=PROVIDER, source="xai_me", fetched_at=datetime.now(timezone.utc),
        title="xAI API access", windows=(window,),
        details=("xAI exposes no subscription-usage API; this shows API access only.",),
        identity=str(me.get("user_id") or "") or None)


def fetch_xai_status(base_url: Optional[str] = None, api_key: Optional[str] = None, *, client=None):
    import httpx
    token = _token(api_key)
    if not token:
        return None
    http = client or httpx.Client(timeout=10.0)
    try:
        resp = http.get(ME_URL, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        resp.raise_for_status()
        return snapshot_from_me(resp.json() or {})
    finally:
        if client is None:
            http.close()


def install() -> bool:
    """Register the fetcher in core's table (idempotent; never replaces a core-owned fetcher)."""
    from agent import account_usage
    table = account_usage._USAGE_FETCHERS
    current = table.get(PROVIDER)
    if current is not None and getattr(current, "__module__", "") != __name__ and \
            getattr(current, "__name__", "") != "fetch_xai_status":
        return False
    table[PROVIDER] = fetch_xai_status
    return True
