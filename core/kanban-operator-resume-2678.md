# Operator `kanban resume` and portable review evidence for the active_pr guard

Patches: **2630–2632**.

## Behavior

- A card that parked on an exact-head review (`dependency_wait` with an authenticated `review_checkpoint`) could stay held `active_pr` forever after the review finished on another gateway: the review card existed on the owner's home file only as a Board replica row, and the parent-evidence mirror required a checkpointed queued move, which capacity or drain moves never carry. Now the owner's own authenticated review park also scopes the mirror, so the home gateway can read the review verdict and release the guard.
- New operator command `hermes kanban resume <id> --reason "..."` for a `ready`, unclaimed card held `active_pr`. It records an `operator_resumed` event (`scope: one_continuation`, `merge_authorized: false`, the latest run's portable identity, actor, reason up to 2000 chars) and a `RESUMED: <reason>` comment, and the dispatcher then lets the owner run once more.
- Refused: under worker lineage (`HERMES_KANBAN_TASK`, `HERMES_KANBAN_WORKER_TASK`, `HERMES_DELEGATED_CHILD_CONTEXT`), with an empty reason, when the card's guard is not `active_pr`, when the card is claimed or not `ready`, on a foreign copy (it must run on the card's home gateway), or if the card changed while the guard was checked. `resume` is also in the delegated-child denied-actions list.
- One continuation: the receipt lapses once a newer run is claimed, so a crash of the resumed run guards again. It never authorizes a merge. A receipt in the same second as the newest PR comment, or with `merge_authorized` not `false`, grants nothing. The operator's own `RESUMED:` comment names a PR but is treated as FYI by the guard.
- A crashed, reclaimed or timed-out owner after its park stays held even when the foreign review result is available, on both foreign sources (the move-parent mirror and the Board replica review path).

## How it works

- 2630: `kanban_move_parent_evidence` gains `_parked_review_checkpoint` (checkpoint of the run's own latest park, matched to the run by portable run uid, never integer ids) and `_parked_scope` (foreign parents named by such a park, limited to children of parents absent from the live file). `prepare()` uses that scope and `result()` accepts either a checkpointed queued move or the park's checkpoint naming the parent. New `hermes_cli/kanban_operator_resume.py` (`resume`, `resumed_after`, `cmd`); the guard evaluates before the write lock and the event-tip is fenced. In `kanban_db_dispatch_pr_state`, the handoff checks are extracted into `_handed_off_after`, which now includes `resumed_after`. `kanban_parser.py` adds the subcommand; docs list it in the CLI reference and Kanban guide.
- 2631: `_completed_review_run` first calls new `owner_faulted_since` (shared with `result()`), so neither foreign source can release a faulted owner. `kanban.py` imports `kanban_operator_resume.cmd` directly instead of a wrapper.
- 2632: the handler is registered through `kanban_parser`'s import so `kanban.py` gains no net line (file line cap).

## Code

`hermes_cli/kanban_operator_resume.py` (new), `hermes_cli/kanban_move_parent_evidence.py`, `hermes_cli/kanban_db_dispatch_pr_state.py`, `hermes_cli/kanban_dependency_review.py`, `hermes_cli/kanban.py`, `hermes_cli/kanban_parser.py`, `website/docs/reference/cli-commands.md`, `website/docs/user-guide/features/kanban.md`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_operator_resume.py`, `tests/hermes_cli/test_kanban_review_checkpoint_cross_machine.py`, `tests/hermes_cli/test_kanban_move_parent_evidence.py`, `tests/hermes_cli/test_kanban_move_pr_handoff.py` (all from 2630). Entry path: the `hermes kanban` argparse entry (`resume` subcommand) plus `check_respawn_guard` and `dispatch_once` for the resume tests; the guard on a home gateway with a Board replica for the cross-machine tests; the mirror's `prepare`/`result` for the evidence tests. Covered: resume releases once and is recorded, the resumed run's crash re-arms the hold; worker lineage via two env vars refused and records nothing; reason and `active_pr` required; same-second and forged receipts grant nothing; foreign review verdict releases the guard, no exact head still holds, a crash after the park still holds; park-scoped mirror without a move checkpoint. 2631 and 2632 add no test file of their own: the crash-after-park case is covered only by the existing foreign-review test above. A dedicated test of the Board-replica review path's fault check, the `HERMES_DELEGATED_CHILD_CONTEXT` refusal, and the foreign-copy refusal are **untested**.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- `resume` only works from an operator shell on the home gateway; it is not a worker tool.
- It does not bypass a different guard (`resume requires an active_pr hold`).
- The receipt names the latest run; if that run changes before the dispatcher acts, the receipt no longer matches and the hold returns.

## Plugin

Core change; no plugin required.
