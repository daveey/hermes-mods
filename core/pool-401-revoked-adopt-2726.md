# Pool 401 revoked: adopt pushed replacements; a recoverable 401 requeues instead of failing

Patches: **2725**.

## Behavior

Background: when a minter (the hub's credential relay) re-mints pooled OAuth accounts, the tokens every worker holds are revoked until its next pull. In one incident, workers rotated through the pool (some accounts revoked, others quota-limited), ran out of entries, and surfaced the last error, a 401, as `failure_reason=auth`. That booked exit 78 (terminal provider error), and the dispatcher gave 31 cards up on the first death.

- When a 401 refresh and rotation both fail, the agent now re-reads `auth.json` for every auth-benched, refresh-less OAuth pool row, and continues the turn on a replacement that has landed meanwhile (log line: `Credential 401 — pool exhausted; adopted pushed replacement for pool entry <id>`).
- A row benched on a 401/403 records the token generation it failed on (`bench_token_fp`). A row whose newer token arrived through the pool's persist merge is no longer treated as revoked.
- If recovery finds nothing, the non-retryable auth result carries `auth_pool_transient` when the wall is not permanent: some sibling is only quota/billing-benched, cooling down for the model, untried, or has a different token on disk. A Kanban worker then exits with the rate-limit code (requeue, no failure counted) rather than 78. A pool with fewer than two enabled entries, or where every entry is auth-dead after the re-read, stays terminal (78).

## How it works

`agent/credential_pool_pushed.py` gains `adopt_pushed_replacements(pool, model)` (walks auth-benched, refresh-less OAuth rows, adopts a pushed replacement via the pool's existing pushed-row adoption, or clears a bench that predates the held token unless a live quota mark exists, and skips rows in a model cooldown) and `auth_wall_is_transient(pool)`. `agent/credential_auth_recovery.py` calls the former after a failed rotation. `agent/credential_pool.py` stores `bench_token_fp` when marking an entry exhausted. `agent/turn_recovery.py` sets `auth_pool_transient` on auth failures, and `hermes_cli/turn_exit.py` maps `auth` plus `auth_pool_transient` to `KANBAN_RATE_LIMIT_EXIT_CODE` for Kanban workers. The entry also refreshes the plugin-hooks cache fixture fingerprint (`fixture/cache/plugin_hooks.json`).

## Code

`agent/credential_auth_recovery.py`, `agent/credential_pool.py`, `agent/credential_pool_pushed.py`, `agent/turn_recovery.py`, `hermes_cli/turn_exit.py`, `fixture/cache/plugin_hooks.json`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_pool_auth_wall.py` (4 tests). Entry path: a real credential pool file under a sandboxed home, the `recover_with_credential_pool` recovery, `nonretryable_client_error_result` with `turn_exit_code`, and the real Kanban dispatcher's worker-death handling, with only the SDK client builder stubbed. Cases: rotation to a healthy sibling, revoked account beside quota-benched ones (exit is the rate-limit code, card returns to ready with no failure), a replacement pushed mid-turn adopted, and all accounts revoked after a re-read (exit stays terminal, card given up). Providers other than the one in the tests, 403 handling, and the bench-predates-token path without a pushed row are **untested**.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Only refresh-less OAuth rows (renewed solely by a push) are re-read; rows with a refresh token follow the normal refresh path.
- A requeued card retries until the wall clears; a permanently bad pool that still has one quota-benched sibling is requeued rather than parked until that sibling's state changes.

## Plugin

Core change; no plugin required.
