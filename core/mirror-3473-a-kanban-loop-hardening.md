# Kanban background loops: CI sweep, remote-notice claims, quota proof gate

Patches: **3425, 3431, 3433**.

## Behavior
- The CI-run sweep never raises out of its scope bind: a refused scope bind or connect error is logged and yields no results instead of escaping the wake-check tick.
- The remote-notice collector can no longer strand a claim when its own "claimed by the live poller" logging fails. A healthy open-chat skip logs at DEBUG; it logs INFO only when the notice is still unclaimed past the waker's grace window.
- Quota recovery no longer benches an account for the 10-minute proof-gate window without a failed proof: no reservation after the deadline, rows released if the deadline passes during reservation, and a late successful proof releases its row (it still never clears the bench). A `SubprocessError` from the resolver spawn reads as proof failed.

## How it works
3425 wraps bind + connect + sweep in `_sweep_db` in a logged `except Exception: return []`, and tightens the per-home warning test so both homes share a board db. 3431 moves the claim log after the append, makes `_note_wait` never raise (lock-guarded bookkeeping, `pop(k, None)` eviction), and downgrades the healthy skip log. 3433 checks the deadline before reserving proof rows and settles each proof future with a done callback that releases the row on success or unstarted cancel.

## Code
- `hermes_cli/kanban_ci_progress.py`
- `tui_gateway/kanban_remote_notices.py`
- `hermes_cli/kanban_quota_recovery.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_ci_progress.py`: `test_refused_scope_bind_does_not_escape_the_wake_check_tick`.
- `tests/tui_gateway/test_kanban_remote_notices.py`: `test_collect_still_offers_its_claim_when_the_wait_log_raises`, `test_open_chat_skip_logs_info_only_when_unclaimed_past_the_grace_window`, `test_sweep_names_its_own_in_flight_claim_and_the_gateway_fifo_once`.
- `tests/hermes_cli/test_kanban_quota_recovery.py` and `tests/hermes_cli/test_kanban_quota_dns_deadline.py`: `test_completion_returns_false_when_resolver_spawn_raises_subprocess_error`, `test_expired_deadline_reserves_no_proof`, `test_deadline_lost_during_reservation_releases_rows`, `test_successful_late_proof_releases_its_row`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
