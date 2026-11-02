# Authenticated dependency-review repair continuation

## Behavior

Patches **0383, 0384** let an owning worker resume once after independent review requests changes to its existing PR. A dependency park must capture the authenticated owner run, parent IDs and PR URLs. Completed, actually claimed parent runs must supply structured `changes_required`, PR and full reviewed-SHA evidence after the park. This is permission to repair, not merge approval or current-head CI evidence.

## How it works

`checkpoint` captures scope inside the block transaction. `resumes_review` validates the latest run, unchanged dependencies, completed review provenance and the guarded PR set. The next claim consumes the permission. Legacy unauthenticated parks, comment-only evidence and changed PR/parent scope fail closed. Patch 0384 moves transactional claim release into `apply_block_update` without changing this contract.

## Code

- `hermes_cli/kanban_dependency_review.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_dispatch_pr_state.py`
- `website/docs/user-guide/features/kanban.md`

## Tests

Executed on the fork revision containing both commits and archive fix `f63cdf362c`, using an existing managed test environment and isolated HERMES_HOME:

`python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_kanban_dependency_review.py tests/hermes_cli/test_kanban_dependency_review_evidence.py`

Result: **13 passed**. These are database/dispatch regression tests; no live dispatcher continuation or Desktop UI was exercised by this mirror verification.

## Applying

Apply 0383 then 0384 after the preceding fork series. They mirror commits `cd68a2ce18` and `cb8fd02e9f`. Activate through the normal safe core update, not by changing an active checkout.

## Gotchas

A repeated PR comment is not new permission. An additional claim consumes the park checkpoint even if the worker later crashes. Do not fabricate checkpoint rows or use scheduling as review authorization.

## Why core rather than plugin?

The ownership check and claim release share the database transaction; a plugin cannot safely recreate this transition afterward.

## Follow-up patch (1542): checkpoint identity survives sync

Patch: `patches/1542-fix-kanban-preserve-review-checkpoint-identity-acros.patch` (`e56d099de52`, "fix(kanban): preserve
review checkpoint identity across sync").

**Behavior.** A review continuation now still works after the board syncs to another gateway. Sync remaps integer
run IDs, so the destination's `dependency_wait` event points at a different local run while its JSON payload still
holds the source's `run_id`. Before this patch, that mismatch kept the card held as `active_pr` on the destination
forever. A change-request review may now record its exact head as either `reviewed_sha` or `head` (a full SHA).

**How it works.** `checkpoint()` selects the whole run row and adds `run_uid` (the run's stable sync UID) to the
evidence when the row has one. `resumes_review()` uses `_checkpoint_matches_run`:
- If `run_uid` is present, it must be a non-empty string equal to the parked run's `uid`. An invalid or mismatched UID
  never falls back to an integer `run_id` that happens to collide.
- If there's no `run_uid` (a legacy or local-only checkpoint), the integer `run_id` must match exactly. A remapped
  legacy checkpoint stays held.

`_reviewed_prs` uses the existing `_exact_head` helper, which accepts `reviewed_sha` or `head`. The rule that the next
claim consumes the permission is unchanged.

**Code.** `hermes_cli/kanban_dependency_review.py`.

**Tests** (in the commit; not run for this mirror): `tests/hermes_cli/test_kanban_dependency_review_sync.py`, which uses
two real boards with real sync outbox/apply:
- `test_sync_remap_preserves_one_review_continuation`: after the remap, `changes_required` and `approved` (each with
  `reviewed_sha` or `head`) allow exactly one continuation, and a claim or crash reclaim brings back the hold.
- `test_local_change_request_accepts_either_exact_head_key`
- `test_uid_mismatch_never_falls_back_to_matching_integer` (UID of `None`, `""`, `42` or an unrelated value)
- `test_synced_invalid_review_or_owner_stays_held` (other PR, short head, other assignee, unowned park)
- `test_legacy_checkpoint_only_accepts_original_local_id`
- `test_real_other_run_uid_cannot_authorize_owner_checkpoint`

**Applying.** Depends on the preceding series. Apply the full ordered series per the README "Applying" section.
Standalone application is not certified.

**Gotchas.** Checkpoints written before this patch have no `run_uid`. They resume only on the board that created them;
after a sync they stay held until a fresh authenticated park. Don't hand-edit `run_uid` into a payload: a wrong value
fails closed.
