"""Account-usage fetcher for the ``gemini`` provider (Google AI Studio API keys).

AI Studio publishes no usage / quota API for API keys. What a key CAN do for free is list models:
``GET /v1beta/models?pageSize=1`` (no tokens billed). So the snapshot has one ``API key`` window:
0% = key accepted, 100% = key rejected (400/401/403). That is a status, not a quota gauge.

Pooled keys (GOOGLE_API_KEY, GEMINI_API_KEY, ...) are each fetched through the per-entry path with
``fetcher(base_url, api_key)``, so the key arrives as the second argument. The key is only sent in
the ``x-goog-api-key`` header and is never logged or returned. A future Gemini subscription/OAuth
provider should be its own slug with its own fetcher, not an extension of this one.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

PROVIDER = "gemini"
MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1"
VALID_DETAIL = "key valid (AI Studio, billed per token or free tier)"
REJECTED_DETAIL = "key rejected"


def snapshot_from_response(status_code: int):
    """Pure: HTTP status of the models call -> AccountUsageSnapshot, or None when inconclusive
    (429/5xx/etc. say nothing about the key; exported for tests)."""
    from agent.account_usage import AccountUsageSnapshot, AccountUsageWindow
    if status_code == 200:
        used, detail = 0.0, VALID_DETAIL
    elif status_code in (400, 401, 403):
        used, detail = 100.0, REJECTED_DETAIL
    else:
        return None
    window = AccountUsageWindow(label="API key", used_percent=used, reset_at=None, detail=detail)
    return AccountUsageSnapshot(
        provider=PROVIDER, source="gemini_models", fetched_at=datetime.now(timezone.utc),
        title="Gemini API key", windows=(window,),
        details=("AI Studio exposes no usage API for API keys; this shows key status only.",))


def fetch_gemini_status(base_url: Optional[str] = None, api_key: Optional[str] = None, *, client=None):
    import httpx
    key = str(api_key or "").strip()
    if not key:
        return None
    http = client or httpx.Client(timeout=10.0)
    try:
        resp = http.get(MODELS_URL, headers={"x-goog-api-key": key, "Accept": "application/json"})
        return snapshot_from_response(resp.status_code)
    finally:
        if client is None:
            http.close()


def install() -> bool:
    """Register the fetcher in core's table (idempotent; never replaces a core-owned fetcher)."""
    from agent import account_usage
    table = account_usage._USAGE_FETCHERS
    current = table.get(PROVIDER)
    if current is not None and getattr(current, "__module__", "") != __name__ and \
            getattr(current, "__name__", "") != "fetch_gemini_status":
        return False
    table[PROVIDER] = fetch_gemini_status
    return True
