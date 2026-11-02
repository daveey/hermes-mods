# Kanban recovery park identity stays causal across reconcile restores

Patches: 1611 1612

- `patches/1611-fix-kanban-keep-recovery-park-identity-causal-across.patch`
- `patches/1612-fix-kanban-goal-triage-hold-keeps-local-order-on-leg.patch`

Related: [kanban-home-gateway-maintenance.md](kanban-home-gateway-maintenance.md) (1576 there added the `park_event_uid`
recovery link that 1611 extends with a causal identity); [kanban-goal-handoff-judge.md](kanban-goal-handoff-judge.md)
(the goal-mode judge whose `goal_unachievable` park and receipt 1611 keeps valid across restores).

## Behavior

Sync reconcile restores a lost `task_events` row by inserting it again under a newer local id, keeping its `uid`.
Recovery authority used to read insertion order (`MAX(id)`, "created id > park id", "receipt id < park id"), so a
restore could change who is allowed to resume a parked card.

- **1611 (causal park identity)**, on synced boards (`task_events` has a `uid` column):
  - Restoring the current `continuation_exhausted` park keeps its existing recovery card authorized. No second
    recovery card is created, and that card's `resume` request executes once (one `unblocked` event on the service
    board).
  - Restoring the `created` event of a stale recovery card (made for an earlier park) does not make it fresh. It is
    still refused with "predates ... current continuation_exhausted park".
  - Restoring an older park does not replace the current one. `_park_event_id` and `open_recovery_card` still return
    the current park and its card.
  - Restoring the `continuation_exhausted` receipt, even with every event's `created_at` set equal, keeps the
    receipt-before-park check passing. An unrelated card is still refused ("creator chain"), and a human gate is
    still refused ("human_gate").
  - Restoring a goal-mode `goal_unachievable` park and its receipt keeps `current_park` true and the repair card
    authorized.
  - Legacy synced history (the park has no causal evidence) fails closed for authority: `exhaustion_refusal`
    refuses, no recovery card is made, and an existing recovery card's `refusal` is non-None. The triage hold still
    keeps the existing recovery card (`open_recovery_card` returns it).
  - Unsynced boards (no `uid` column) keep local-id order unchanged. Untested.
- **1612 (goal triage hold keeps local order)**: `goal.current_park` is a hold that keeps a judged goal out of
  generic triage, not a grant of authority. On 1611 it returned true only when the unique causal head *was* the
  goal block, so any of these released the hold. They no longer do:
  - a later non-block lifecycle event on the parked goal (for example `ask_rechecked`);
  - a legacy synced goal park with no causal evidence;
  - forked lifecycle heads (two events written without seeing each other).

## How it works

- New module `hermes_cli/kanban_recovery_history.py`. `_append_event` passes every payload through `stamp()`. On
  synced boards, lifecycle kinds (`_LIFECYCLE_KINDS`), receipt kinds (`continuation_exhausted`,
  `goal_unachievable`) and `created` get a `_recovery_history_v1` payload key:
  - `identity`: a fresh UUID, written once and carried unchanged by sync;
  - lifecycle events: `predecessors`, the identities of the task's current lifecycle heads;
  - `blocked` with a run: `receipts`, the identities of that run's receipt events;
  - `created`: `parks`, the current park identity of each card up the creator chain (`MAX_CREATOR_HOPS`).
- `synced(conn)` checks the schema (`uid` column present), not the sync toggle, because a detached board can still
  hold replayed history.
- `heads()` returns the lifecycle rows that no other row names as a predecessor. `latest()` / `head()` return the
  unique evidenced head, or `None` when history is legacy or forked. On unsynced boards it returns the max-id row.
- Readers switched from id order to evidence:
  - `exhaustion_refusal` uses `history.latest`. The receipt check is `receipt_precedes`: the receipt's identity is
    in the park's `receipts` list (local `id <` on unsynced boards).
  - `_recovery_refusal` freshness is `history.fresh`: the requester has exactly one `created` event, and its
    `parks[target]` equals the target's current park identity.
  - `_park_event_id` uses the evidenced head when one exists. If no lifecycle event of the target is evidenced, it
    falls back to `MAX(id)` so the triage hold keeps its old local anchor.
  - `kanban_goal_recovery.valid_receipt(before=...)` uses `receipt_precedes` against the block row.
  - `kanban_resume_requester` builds the idempotency key as `recovery:<target>:<park identity>` (the local id on
    unsynced boards). The `resume_requester_created` event also records `park_identity`. `_event_for_park` matches
    on `park_identity` first, then on `park_event_uid` (1576), then on the legacy local-id link.
- **1612**: adds `history.nearest(conn, target, kinds)`, which returns `(decided, row)`. It walks back from the
  unique head through `predecessors` to the nearest `blocked` event. It is undecided (`False, None`) when there is no
  unique evidenced head, when it meets an unevidenced ancestor or a predecessor that is not present, or when two
  candidates appear at the same step. `current_park` uses the causal answer only when it is decided; otherwise it
  uses the old local-order query (newest `blocked` by id).

## Code

- `hermes_cli/kanban_db.py` (`_append_event` calls `stamp`)
- `hermes_cli/kanban_recovery_history.py` (new in 1611; `head`/`nearest` added in 1612)
- `hermes_cli/kanban_goal_recovery.py` (`valid_receipt`, `current_park`)
- `hermes_cli/kanban_resume_exec.py` (`exhaustion_refusal`, `_park_event_id`, `_recovery_refusal`)
- `hermes_cli/kanban_resume_requester.py` (`_key`, `_event_for_park`, `ensure_recovery_cards`)

## Tests

`tests/hermes_cli/test_kanban_recovery_reconcile.py` (new in 1611, extended in 1612):

- **1611**: `test_restored_current_park_keeps_requester_and_executes_once`,
  `test_restored_stale_creation_does_not_gain_authority`, `test_restored_historical_park_does_not_replace_current`,
  `test_restored_receipt_and_equal_timestamps_keep_causal_order`,
  `test_legacy_synced_park_without_provenance_fails_closed`,
  `test_legacy_synced_history_keeps_triage_hold_but_not_authority`,
  `test_goal_park_and_receipt_restore_without_changing_judge_evidence`.
- **1612**: `test_later_lifecycle_event_keeps_goal_specifier_hold`, `test_legacy_synced_goal_park_keeps_specifier_hold`,
  `test_forked_lifecycle_heads_keep_goal_specifier_hold` (helper `_goal_park`).

The restore tests delete a row without sync capture and run the real `kanban_sync_client.reconcile` against the
test hub. They assert that the restored row's local id is larger than the original.

These were not rerun by the mirror pass.

Untested:
- Unsynced boards (no `uid` column) keeping local-id order in `latest`, `park_identity`, `receipt_precedes`,
  `_recovery_refusal` and `current_park`.
- `nearest` stopping at an unevidenced ancestor in the middle of the walk, or at a missing predecessor. The tests only
  cover a missing head (all evidence stripped) and forked heads.
- `nearest` returning decided-with-no-block (`True, None`), so `current_park` returns false.
- `_event_for_park` matching on `park_identity` for a requester created before a restore and read from a foreign
  copy.
- `parks` evidence on cards more than one creator hop from the parked target.

## Applying

- Apply 1611 then 1612; 1612 rewrites `latest()` from 1611 and depends on it.
- Needs the recovery-requester / park-uid patches from
  [kanban-home-gateway-maintenance.md](kanban-home-gateway-maintenance.md) (1570, 1576: `_park_uid`,
  `park_event_uid`), plus the sync-client `uid` column and `reconcile`.
- No config. Evidence is written only to new events after the upgrade. Existing history stays legacy: it fails
  closed for resume authority and keeps the local-order triage hold.

## Gotchas

- After the upgrade, a card parked before it cannot be resumed by its recovery card on a synced board until a new
  evidenced park is written (by design, fail closed). Expect `exhaustion_refusal` refusals on old parks.
- Every stamped event payload gains a `_recovery_history_v1` key. Tools that compare event payloads byte for byte
  will see it.
- `stamp` runs a `PRAGMA table_info` and a lifecycle-events scan for each stamped append, and walks the creator
  chain for each `created`. Other event kinds return before any query.
- The idempotency key format changed to `recovery:<target>:<uuid>` on synced boards. Keys from before the upgrade
  (local ids) do not dedupe against new ones by key alone.

## Plugin?

No. The change is in core `_append_event` and the recovery authority readers in `hermes_cli`, and no hook covers
them.
