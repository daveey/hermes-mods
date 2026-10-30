# Settle obsolete remote-notice claims without stale delivery

## Behavior

Patch **1131** settles a pending remote Kanban notice when its holder is dead and the canonical replica card has left the notice's recorded state. It acknowledges the old claim without writing its obsolete text into the chat. Live holders, unchanged card states, and cards absent from the replica are left alone by this branch.

## How it works

The remote-notice sweep now calls `_settle_stale_claims` over all canonical replica rows, not just currently terminal cards. `_current_states` constructs ledger-compatible state keys, including terminal timestamps. For an undelivered claimed notice with a changed state, the sweep checks holder liveness and conditionally updates the matching ledger row: `delivered=1`, cleared holder fields, `legacy_seen=1`, and `settled='stale'`.

`_stale_notice_id` derives the profile-aware notice identity, and settled identities are recorded in `swept` and remembered as delivered. `_ledger` migrates an optional `settled` text column. Missing replica evidence is conservative: it does not imply that a card moved on. Errors are logged at DEBUG and return zero settled claims.

## Code

`tui_gateway/kanban_remote_notices.py`: `_ledger`, `_current_states`, `_stale_notice_id`, `_settle_stale_claims`, and the call in `sweep_remote_notices`.

## Tests

Tests identified from source, **not executed in this documentation pass**:

`tests/tui_gateway/test_kanban_remote_notices.py::test_sweep_settles_a_dead_holder_claim_whose_card_left_that_state` constructs moved/dead-holder, still-blocked/dead-holder, and moved/live-holder cases. It asserts no stale transcript text, settled ledger fields, the exact swept identity, preservation of the other claims, and an idempotent second sweep. The absent-replica skip is visible in the code but is not a separate case in the newly added test.

This is an isolated replica/ledger/session-store fixture, not a live multi-gateway delivery test.

## Applying

Replay 1131 with the full ordered fork series and preceding remote-notice claim work (`git am -3 --empty=keep` from the documented base). It depends on existing holder liveness, ledger, and swept-record infrastructure. Independent application and execution were not tested here.

## Gotchas

`delivered=1` with `settled='stale'` means intentionally discarded as obsolete, not delivered to the user. A dead holder on a still-current terminal state remains eligible for the existing collection/re-offer path; this settlement does not replace it.

## Plugin

No plugin is introduced. The change is in the core remote-notice ledger/sweep, which owns delivery deduplication.
