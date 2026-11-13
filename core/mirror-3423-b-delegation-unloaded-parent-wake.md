# Async delegation: wake an unloaded parent chat

Patches: **3006–3009**.

## Behavior
- A terminal async-delegation result that no live poller delivers now reaches its parent chat without reopening it: past a 90 s grace and not under a live delivery claim, it is claimed in the ledger and run as one turn in the parent (resumed if no backend holds it).
- Busy, refused or error outcomes defer with backoff; a chat held by another backend or with no store is left pending; a per-chat wake budget applies.
- Archived or moved parent chats are never woken (left pending, backed off).
- Dead-owner recovery runs at most every 300 s per ledger.

## How it works
- 3006 adds `tui_gateway/delegation_unloaded.py`, run from the unloaded-session sweeper in `kanban_unloaded.py`, with ledger helpers in `tools/async_delegation.py`.
- 3007 adds the `chat_skip_reason` gate (skipped:archived / skipped:moved) and a warning when the ack finds a lapsed claim.
- 3008 rate-limits `recover_abandoned_delegations` via `RECOVER_EVERY_S`.
- 3009 reaches display metadata through `server` to satisfy the split-module import rule.

## Code
- `tui_gateway/delegation_unloaded.py`
- `tui_gateway/kanban_unloaded.py`
- `tools/async_delegation.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tui_gateway/test_delegation_unloaded_wake.py`: `test_unloaded_parent_gets_one_turn_and_the_row_is_delivered`, `test_a_live_claim_wins_and_the_sweeper_never_doubles_it`, `test_a_claim_taken_between_select_and_claim_is_left_alone`, `test_a_wake_that_did_not_start_defers_without_spending_an_attempt_and_backs_off`, `test_a_chat_no_store_has_is_left_pending`, `test_a_chat_held_by_another_backend_is_left_to_it`, `test_a_chat_mid_turn_here_waits_for_its_own_poller`, `test_fresh_results_and_gateway_results_are_not_the_sweepers`, `test_the_wake_budget_caps_turns_per_chat`, `test_tail_repro_copy_in_another_backend_starves_the_holders_orphan_sweep`, `test_the_kanban_unloaded_loop_runs_the_delegation_pass`, `test_end_to_end_unloaded_parent_is_resumed_here_and_runs_the_result_turn`, `test_an_archived_or_moved_parent_is_never_woken`, `test_a_lapsed_claim_at_ack_is_logged_as_a_warning`, `test_dead_owner_recovery_runs_at_most_every_recover_every_s`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
3009 has no tests of its own. Runs in Desktop/TUI backends only (via the sweeper loop).
