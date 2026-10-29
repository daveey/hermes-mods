# Anthropic pre-request token check stays on the pinned pool account

Patches:

- `patches/0579-fix-anthropic-pre-request-token-check-stays-on-the-p.patch`: the per-request credential check reads only the pool entry the session is pinned to; model-aware pool resolution skips expired entries; 401 recovery warns when the failing key is another entry's.

What you see: with several Claude accounts in the credential pool, a session that rotated to account B keeps sending B's token. Before, every request swapped the first pool entry's token back in; when that token had expired the turn looped on "OAuth access token has expired", refreshed B twice for nothing (spending B's single-use refresh tokens) and gave up ("Non-retryable client error" or a fallback model).

## Behavior

- `_try_refresh_anthropic_client_credentials` runs before every Anthropic request. When the agent has a credential pool and a pinned `_credential_pool_entry_id`, it now resolves only that entry: it adopts a rotation another process persisted for it (pool store, or the Claude Code file for the borrowed `claude_code` row) and refreshes it when expiring. It never returns another account's token. If that entry's refresh fails, the current key is kept and 401 recovery decides.
- Unpinned sessions keep the old resolver, but the pool entry id is rebound to whatever key was adopted (as the `.env` refresh path already did), with a WARNING when that moves the session off its entry.
- `resolve_anthropic_token(model=...)` (API-call paths) skips pool entries whose access token is past its stamped expiry. Model-less diagnostic calls (usage display, `hermes models`) keep the read-only first-entry result.
- 401 pool recovery logs `Credential auth failure: the failing key belongs to pool entry X, but the session is attributed to entry Y` when the key and the attributed entry disagree, the signature of this drift.

## How it works

- `agent/credential_pool_pin.py`: `pinned_anthropic_pool_token(pool, credential_id) -> (found, token)` and `warn_foreign_failing_key(pool, api_key_hint, credential_id)`.
- `agent/client_lifecycle.py`: `_resolve_anthropic_refresh_token()` picks pinned vs legacy resolution; `_adopt_anthropic_token()` is the unchanged client rebuild + key propagation, split out.
- `agent/anthropic_credentials.py`: `_resolve_anthropic_pool_token(model=...)` expiry skip.
- `agent/agent_runtime_helpers.py`: `_recover_auth_failure` calls the drift warning before refreshing.

## Code

`agent/credential_pool_pin.py`, `agent/client_lifecycle.py`, `agent/anthropic_credentials.py`, `agent/agent_runtime_helpers.py`.

## Tests

`tests/agent/test_anthropic_pool_pin.py` (real pool store in a temp `HERMES_HOME`; only the token endpoint and SDK client builder are stubbed), all 7 red on the base:
- pinned to B with an expired A first: the request client carries B's key and id;
- after recovery refreshes B and swaps it in, the next request carries B's new token;
- B rotated by another process (pool store): adopted;
- B expiring: B is refreshed once (its own refresh token), never swapped for A;
- `resolve_anthropic_token(model=...)` skips the expired first entry; the model-less resolver does not;
- pinned entry removed from the pool: the id is rebound to the adopted key, with the warning;
- 401 recovery with A's key attributed to B logs the drift warning.

Live replay on a copy of hermes-hub's pool (refresh POST blocked): pinned to `df70c1` with `100d65` first and expired, the base sent `100d65`'s key on all 3 requests while the id said `df70c1`; the fix kept `df70c1` on all 3.

## Applying

Standalone on top of the credential pool. Independent of the model-routing class refresh, which still runs for unpinned sessions.

## Gotchas

- A session pinned to a revoked account stays on it until 401 recovery rotates; the 2-refresh cap and fallback still apply.
- The pinned check reads the pool store once per request (a single-row read; the old path reloaded and re-seeded the whole pool).

## Plugin?

No. The pre-request check is inside the agent's client lifecycle; there is no seam for it.
