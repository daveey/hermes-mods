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

## Follow-up patch (1645): review reassignment chronology across sync (test only)

**1645** (`4508013661`, `test(kanban): pin review reassignment chronology`).

- **Behavior covered.** After a completed `changes_required` review, reassigning the card to another assignee and
  syncing it never lets the new owner resume the old owner's review checkpoint (`resumes_review` is false on the
  replica). Whether the respawn guard still holds depends on chronology: an assignment strictly later than the PR
  publication is an independent operator handoff and is released (`None`); an assignment in the same second keeps
  `active_pr`. A `review_clock` fixture pins `time.time` so scheduling delays cannot pick which policy runs; the
  existing `test_synced_invalid_review_or_owner_stays_held` now uses it too. No production code changes.
- **Tests** (not rerun by this mirror pass): `tests/hermes_cli/test_kanban_dependency_review_sync.py::test_synced_reassignment_separates_review_from_operator_handoff`
  (`assignment_delay` 0 and 1).


## Follow-up 1873, 1880: same-second parks and PR-naming prerequisites keep the ordering handoff

Patches: **1873** (`f02c12e269`, `[daveey] kanban: a dependency park in the same second as the owner's own PR report resumes it`), **1880** (`0f4792607e`, `[daveey] kanban: a prerequisite that names the child's PR keeps its ordering handoff`). Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

### Behavior

- A dependency park written in the same second as the owner's own PR report still resumes the card when the parent finishes (1873).
- A prerequisite whose result names the child's checkpointed PR keeps the ordering handoff instead of being read as a review (1880).

### How it works

`hermes_cli/kanban_db_dispatch_pr_state.py` and `hermes_cli/kanban_dependency_review.py` compare park and PR-comment times inclusively and classify prerequisites by role, not by PR mention.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1873: An owner that posts its state comment (with the PR URL) and then parks on a dependency in the same turn lands both rows in one wall-clock second. The ordering-park handoff required parked_at > newest PR comment time, so the tie kept the card held by active_pr after its parent finished (t_01a19e1fc70f3521, comment 33793 and dependency_wait both at 1793663272), filing a respawn-guard fix card and needing a manual reset.
- 1880: Problem: since #728 (f3dea5638be) a parent result that names the child's checkpointed PR counts as a review. The ordering handoff then refuses, the child stays held by active_pr and a "Fix respawn-guard hold" card is filed. Replaying the classifier over every dependency_wait checkpoint on hermes-hub flips 27 of 301 parent/child pairs from ordering to review. 21 of them report their own work (CI gates, merges, implementations, triage). 6 are reviews or size-judge rulings that record their outcome under keys other than verdict/approved/reviewed_sha. The pre-#728 rule (581a96495866) classified...

### Code

`hermes_cli/kanban_db_dispatch_pr_state.py`, `hermes_cli/kanban_dependency_review.py`.

### Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/hermes_cli/test_kanban_dependency_pr_handoff.py`: `test_same_second_park_after_own_pr_report_resumes_once`, `test_same_second_pr_comment_after_park_stays_guarded`, `test_same_second_park_without_valid_watermark_stays_guarded`
- `tests/hermes_cli/test_kanban_dependency_review_no_verdict.py`: `test_lost_review_citing_its_report_file_stays_held`, `test_lost_review_completed_by_the_worker_tool_stays_held`, `test_lost_review_needs_a_checkpointed_pr_to_name`
