# Notice fan-out transaction rollback and shared executor-cancel prefixes

Patches: **3578–3579**.

## Behavior
- A non-SQLite exception raised mid-write in the notice fan-out store (`seed_remote` / `ack_remote`) no longer leaves the cached connection inside an open write transaction. The write lock is released, later cursor writes are visible to other connections, and the original exception still propagates.
- Restart-continuation classification of the executor's cancel/skip result text now uses a single shared prefix tuple, so the restart-Stop path and the update-pause path cannot drift apart. No behavior change.

## How it works
3578 makes the store's `_open()` context roll back on exit whenever the cached per-(path, pid) connection is still `in_transaction`, and drop the connection if the ROLLBACK itself fails. Previously `close()` no longer rolled back once the connection was cached, so a `TypeError` from mixed-type cards between `BEGIN IMMEDIATE` and `COMMIT` kept the RESERVED lock.

3579 renames the executor-cancel prefix tuple in `agent/update_pause_cancel.py` to public `EXECUTOR_CANCEL_PREFIXES` and has `gateway/session_kanban_continue.py` import it instead of keeping its own `_STOP_RESULT_PREFIXES` copy. It also documents, in a test comment, that the own-result form of a mid-tool restart is a wall-clock race, and pins that form to the killed command's envelope when present.

## Code
- `tui_gateway/notice_fanout_state.py`
- `agent/update_pause_cancel.py`
- `gateway/session_kanban_continue.py`
- `tests/tui_gateway/test_kanban_unloaded_host_lease.py`
- `tests/gateway/test_kanban_restart_continuation.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tui_gateway/test_kanban_unloaded_host_lease.py`: `test_notice_fanout_state_a_non_sqlite_error_mid_transaction_leaves_no_open_write` (parametrized over the seed and ack paths).
- `tests/gateway/test_kanban_restart_continuation.py`: modified existing `test_a_real_owner_restart_mid_turn_continues_the_same_run` (mid-tool-call case).

The prefix-tuple refactor has no dedicated new test (untested).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
The own-result form of a mid-tool restart depends on the executor's 3 s wait for the interrupted call, so it is timing dependent; the test accepts the bare stamp as well.
