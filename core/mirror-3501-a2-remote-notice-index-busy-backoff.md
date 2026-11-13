# Remote notice index: busy is not corrupt, holder backoff, role upgrade

Patches: **3493**.

## Behavior
- A locked/busy notice index copy is no longer deleted as corrupt. Publishing counts it in `stats["busy"]` and returns False; readers compute locally as on any cache miss.
- Only genuinely corrupt or not-a-database files are recreated.
- A holder whose refresh fails (or raises) backs off, doubling from the poll interval up to 60 s; success resets the backoff.
- No holder is started when `fcntl` is unavailable, since publishing needs flock.
- If the gateway claims the lease after a lower-priority holder (serve) already started, the lease is upgraded to the higher role.

## How it works
3493 narrows the exception handling in `_open_rw` to SQLITE_CORRUPT / SQLITE_NOTADB (with message fallbacks "file is not a database" and "malformed"); other database errors, including "database is locked", propagate to `publish`. `_hold` now tracks failures with an injectable clock and exponential backoff capped at `BACKOFF_MAX_S`. `_holders` records role priority so a later higher-role `ensure_holder` call stops the lower refresher and starts one under the new role. Call-site comments in the owner and notices modules were corrected.

## Code
- `tui_gateway/kanban_remote_index.py`
- `tui_gateway/kanban_remote_notices.py`
- `gateway/kanban_remote_owner.py`
- `tests/tui_gateway/test_kanban_remote_index.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tui_gateway/test_kanban_remote_index.py`: `test_busy_index_is_skipped_not_deleted_as_corrupt`, `test_holder_backs_off_while_publishing_fails`, `test_no_holder_without_flock`, `test_gateway_claim_upgrades_a_serve_holder`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Role upgrade stops the lower-role refresher and starts a new one; it does not downgrade.
