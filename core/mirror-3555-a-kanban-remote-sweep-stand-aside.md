# Desktop remote-notice sweep stands aside while the gateway's turn holds a chat

Patches: **3533**.

## Behavior
- The Desktop backend's remote kanban notice sweep no longer claims a notice, wakes the chat, or logs repeated "deferred: a turn is running" lines while an unexpired turn lease on the chat's lineage is held by the canonical gateway's verified process.
- After the lease is released, the notice is delivered exactly once even with no gateway owner pass.
- A dead holder, an expired lease, or a live holder that is not the gateway keeps the previous sweep path.

## How it works
3533 makes `_deliver_link` in `tui_gateway/kanban_remote_fifo.py` stand aside (no claim, no wake, one wait line per episode) under those conditions. The canonical gateway pid comes from the live gateway pid for the link profile, else the multiplexed default gateway; owner discovery is never used. It is lease-only (no ledger arm). `kanban_remote_notices.py` is adjusted to match.

## Code
- `tui_gateway/kanban_remote_fifo.py`
- `tui_gateway/kanban_remote_notices.py`
- `tests/gateway/test_kanban_remote_lease_stand_aside.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/gateway/test_kanban_remote_lease_stand_aside.py`: `test_sweep_leaves_a_gateway_leased_busy_chat_alone_no_claim_no_deferred`, `test_gateway_lease_with_no_owner_pass_delivers_exactly_once_after_release`, `test_a_dead_gateway_holder_keeps_the_sweeps_path`, `test_an_expired_lease_of_the_live_gateway_keeps_the_sweeps_path`, `test_a_live_holder_that_is_not_the_gateway_keeps_todays_path`, `test_canonical_gateway_pid_falls_back_to_the_multiplexed_default_gateway`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The owner pass's own cadence is not changed here (split to a separate latency item).
