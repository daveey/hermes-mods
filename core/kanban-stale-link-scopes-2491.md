# Scoped stale linked-card projections with a bounded background refresher

Patches: **2431**.

## Behavior

A warm linked-card read with `stale_ok=True` now returns its last-good projection immediately, before any fingerprint or revision work that could wait on a Python lock. Previously a single cache slot meant a read for another scope (profile/board context) evicted the warm entry and sent the reader into blocking work. Up to 16 scopes are now kept warm. A cold or strict read still builds synchronously, and a failed cold read raises instead of returning an empty list. A failed background refresh keeps the previous projection and logs a warning; the next poll retries. When the replica is unreadable and the ledger fallback also fails, `replica_cards` now raises instead of returning `[]`, and the fallback honors the caller's `stale_ok` policy.

## How it works

`kanban_session_activity._links_cache` changed from one tuple to a dict keyed by scope, capped at `_LINKS_SCOPE_CAP` (16), published copy-on-write so lock-free readers never see an intermediate eviction. `_links_lookup` returns a warm hit first when `stale_ok`, then calls `_refresh_links_in_background`. That submits the scope to `ScopeRefresher` in the new `hermes_cli/kanban_links_refresh.py`: one lazy daemon thread (`kanban-remote-links`), a pending dict shared with active work and bounded by the same capacity, coalescing of repeated polls, and no projection IO under the condition. Each queued job carries a `copy_context()` of the submitter so profile/home/board context variables apply. `_refresh_links_scope` skips publishing when the ambient `_origin_scope()` no longer matches the queued scope. A full queue drops the submit and a later poll retries; a failed thread start clears pending so a retry can start a worker. `_ledger_fallback` in `kanban_chat_links` passes `stale_ok` through to `_remote_links`. See [kanban-session-links.md](kanban-session-links.md) and [links-fingerprint-scope.md](links-fingerprint-scope.md) for the underlying link projection.

## Code

`hermes_cli/kanban_links_refresh.py` (new), `hermes_cli/kanban_session_activity.py`, `hermes_cli/kanban_chat_links.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_links_stale_scopes.py`, `test_kanban_links_stale_controls.py`, `test_kanban_chat_links.py`, `test_kanban_remote_inventory_cache.py`, `test_web_links_stale.py`. Covered paths: the internal `_remote_links` Python API (warm stale, cold, strict, bounds, scope capacity, failure retention) and the dashboard REST route `/api/session-kanban-tasks` through a real ASGI client, with the replica-unreadable ledger fallback. An independently installed `user-steps` plugin router variant in `test_web_links_stale.py` skips when that plugin is absent. Tool and CLI paths are untested here.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Cold and strict reads never use the background worker; only warm `stale_ok` reads can be stale.
- A queued refresh whose scope no longer matches the ambient scope is skipped, so a refresh may be lost if the environment changed in between.
- When the queue holds 16 scopes, extra scopes are not refreshed until a later poll finds room.
- `_links_refreshing` was removed; tests must use `_links_refresher.busy`.

## Plugin

Core change; no plugin required.
