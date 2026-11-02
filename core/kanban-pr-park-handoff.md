# PR guard handoff after an orderly park

Patches: 0350 1533 1603 1610

Patch **0350**, source `1a2ba03afccbee694b5dcb1adee73e85bb42be3d`. Follow-up: **1533** (ordering dependency parks), **1603** (added gates and already-done synced parents), **1610** (stable run identity).

- `patches/1603-daveey-kanban-ordering-park-handoff-survives-operato.patch`
- `patches/1610-fix-kanban-match-ordering-park-checkpoint-by-stable-.patch`

## Behavior

An orderly continuation or scheduled wait strictly after a worker's PR comment releases the `active_pr` respawn hold when that card wakes. A crash is not a handoff, a same-second tie stays guarded, and a newer PR comment re-arms the hold. This changes dispatcher guard decisions, not PR acceptance or merge authority.

The dispatcher PR-state read uses the configured `kanban.pr_acceptance.gh_command_prefix` and platform-resolved GitHub CLI. This supports service processes with a minimal PATH and no ambient login. Setup errors fall back to bare `gh`; failed lookups remain fail-closed.

## How it works

`active_pr_reason` includes `continued` and `scheduled` among candidate handoff events, filtered through the existing handoff predicate and strict timestamp comparison. `_gh_argv` builds an argv list using the PR acceptance prefix resolver and `resolve_gh`. Configuration errors are logged without exception text to avoid credential disclosure.

## Code

- `hermes_cli/kanban_db_dispatch_pr_state.py`: handoff eligibility and broker-aware PR lookup.
- `tests/hermes_cli/test_kanban_review_lifecycle.py`: continuation/schedule handoffs, crash control and newer-comment rearming.
- `tests/hermes_cli/test_kanban_park_invariants.py`: resolved executable and broker argv, setup fallback and failed-lookup behavior.

## Tests

The applied public series passed **68 tests**, with **0 failures** and **1 macOS-only skip**, on Linux using:

```sh
bash scripts/run_tests.sh tests/hermes_cli/test_kanban_park_invariants.py tests/hermes_cli/test_kanban_review_lifecycle.py -j1
```

Tests ran in an isolated standalone clone of the applied series. Broker subprocess calls were mocked; live credentials, actual dispatcher deployment and Desktop rendering were not tested by this mirror run.

The full ordered **277-patch** `git am -3 --empty=keep` succeeded at merge-base `4957bbd0a0f6a1c3fe8217dc4a66d072971f5b63`, with upstream `749cb638ff43f9ce5333c186a1e4f67d473aa4f2` and fork `1a2ba03afccbee694b5dcb1adee73e85bb42be3d`. Prior privacy scrubs and patch 0251's merge adaptation were retained. This patch additionally generalizes private incident details in prose without changing behavior.

## Applying

Apply the complete ordered series from its merge-base. This depends on the earlier PR guard and broker transport work, including patch 0335.

## Gotchas

An orderly park must follow the relevant PR comment; it does not globally disable duplicate-work protection. Missing or broken GitHub access can still hold cards absent a qualifying handoff. This patch is distinct from continuation-budget exhaustion routing and does not implement that separate guard.

## Plugin relationship

This is core dispatcher behavior, not a disk plugin. Publishing the mirror does not restart or deploy any runtime.

---

## Follow-up: an orderly dependency park also hands off once (1533)

Patch `1533-fix-kanban-resume-PR-owners-after-orderly-dependency.patch` (fork `197c0a6bfafe`).

### Behavior

A worker that already published a PR can park its card on an ordering prerequisite with `dependency_wait` (block
`kind="dependency"`). Before this patch, when the prerequisite finished and the card was promoted back to `ready`,
the `active_pr` respawn hold kept the card from ever dispatching again. Now such a park releases the hold for
**exactly one** continuation, the next claim, when all of the following hold:

- **The owner parked it.** The card's latest run was claimed by the card's assignee and ended as `blocked`. The
  park's `review_checkpoint` was stamped by the authenticated `expected_run_id` path for that same run.
- **Order and timing.** The park comes strictly after the newest PR comment. A park in the same second as the
  comment stays guarded. After the park there is no `crashed`, `reclaimed` or `timed_out` event.
- **The parents finished.** A `promoted` event follows both the park and the run's end. The card's parents still
  equal the checkpoint's `parents`, are all satisfied, and include the awaited parents.
- **No review verdict.** No parent's latest run carries review data: metadata keys `verdict`, `approved` or
  `reviewed_sha`, or a verdict line in its summary. Review handoffs remain the job of `resumes_review` and its
  exact-head checks.

Once the next claim happens, the handoff is used up, and later crashes or reclaims are guarded again. The hold is
**not** released by any of these:

- An operator's park of the live run, or an operator's link on a card whose owner run is gone.
- Removing the awaited dependency edge instead of finishing the parent.
- An unfinished parent.

### How it works

`active_pr_reason` calls `_dependency_handoff_after(conn, task_id, max(pr_times))` after the existing
`continued`/`scheduled` handoff check and before `resumes_review`. The helper compares when events **occurred**
(`created_at` epochs) rather than their insertion order, because synced history can append older events after a
promotion. Any event without a timestamp fails closed.

The `dependency_wait` event's `run_id` names the run that was closed, not who closed it. Proof that the owner wrote
the park comes from the run-scoped `review_checkpoint`. The awaited-parent and verdict checks reuse
`kanban_dependency_review._awaited_parents` and `_SUMMARY_VERDICT`.

### Code

`hermes_cli/kanban_db_dispatch_pr_state.py`: `_dependency_handoff_after` and the hook in `active_pr_reason`.

### Tests

`tests/hermes_cli/test_kanban_dependency_pr_handoff.py` is new and uses a real board in `tmp_path`, with `pr_state`
mocked to `OPEN`. **It was read from the diff and not executed in this mirror pass.**

- `test_ordering_dependency_dispatches_once`: the reason is `None` and `dispatch_once` spawns the card exactly once.
  After that claim the hold returns, including after a reclaim.
- `test_failure_after_ordering_park_stays_guarded`: a `crashed`, `reclaimed` or `timed_out` event after the park
  keeps the hold.
- `test_park_before_new_publication_then_crash_stays_guarded`: a newer PR comment followed by a crash keeps the
  hold.
- `test_same_second_park_stays_guarded`: the hold stays.
- `test_unfinished_parent_does_not_release`: the hold stays.
- `test_missing_handoff_evidence_stays_guarded`: the run has the wrong profile, no `ended_at`, outcome `crashed`,
  no `promoted` event, or a park with no `run_id`. Each keeps the hold.
- `test_imported_failure_uses_occurrence_time`: a synced crash dated 1 s before the park releases the hold; one
  dated at or after the park keeps it.
- `test_operator_link_without_owner_run_stays_guarded`: the hold stays.
- `test_operator_park_of_live_owner_run_is_not_owner_intent`: an unfenced `block_task` keeps the hold.
- `test_later_park_cannot_bypass_review_evidence`: a parent with a short-head approval, or with
  `changes_required` on another PR, keeps the hold.
- `test_removing_awaited_edge_is_not_parent_completion`: the hold stays.
- `test_prose_review_without_head_still_requires_review_evidence`: a parent summary of
  `VERDICT: REQUEST_CHANGES` keeps the hold.

### Applying

Apply after 0350 and the dependency-review work, which provides `kanban_dependency_review._awaited_parents`,
`_SUMMARY_VERDICT` and the `review_checkpoint` stamping on dependency parks. Apply in series order. No config is
needed.

### Gotchas

- **One continuation only.** This permits one continuation after the parent finishes. If that successor crashes,
  the PR hold applies again.
- **Ordering parks only.** A dependency park whose parent produced any review verdict is handled only by
  `resumes_review`, never by this ordering handoff.

## Follow-ups: added gates and stable checkpoint identity (1603, 1610)

### Behavior

- **1603:** A completed operator-added ordering gate no longer invalidates an owner's prior park; the dispatcher can launch one continuation. An unfinished added gate or one carrying review evidence still keeps the PR hold. This supersedes 1533's exact-parent-set requirement: checkpointed parents must remain a subset of current parents, and all current parents must be satisfied.
- **1603:** A synced parent with no local task/run can be skipped for review-result inspection only if the checkpoint already included it but did not await it. A remote parent awaited at park time remains guarded even after its synced status becomes done.
- **1610:** An ordering-park checkpoint matches the parked run by stable `run_uid`, so remapping integer run IDs during sync does not invalidate it. A present but invalid or foreign UID stays guarded; only a checkpoint with no UID field falls back to its original integer ID. A subsequent claim consumes the handoff locally and after sync.

### How it works

**1603** validates a nonempty list of string checkpoint parents and uses a subset check plus `unsatisfied_parents`. `_awaited_parents` still validates the awaited dependencies. The missing-result exception requires a checkpointed, non-awaited parent, an `awaited` list, and no local task; it is not permission to ignore missing local run evidence.

**1610** replaces direct checkpoint `run_id` equality in `_dependency_handoff_after` with the shared `kanban_dependency_review._checkpoint_matches_run` predicate. Sync rewrites relational integer IDs but leaves checkpoint JSON unchanged, so the stable UID is authoritative when present.

### Code

- **1603, 1610:** `hermes_cli/kanban_db_dispatch_pr_state.py` — parent/result checks and checkpoint matching in `_dependency_handoff_after`.
- **1603:** `tests/hermes_cli/test_kanban_dependency_pr_handoff.py` — added-gate and synced-parent regressions.
- **1610:** `tests/hermes_cli/test_kanban_dependency_ordering_sync.py` — local and transported checkpoint identity regressions.

### Tests

**1603**, in `tests/hermes_cli/test_kanban_dependency_pr_handoff.py`:

- `test_operator_added_gate_after_owner_park_still_allows_one_continuation`
- `test_operator_added_gate_still_open_stays_guarded`
- `test_operator_added_review_gate_cannot_bypass_review_evidence`
- `test_synced_parent_done_before_park_does_not_hold_ordering_continuation`
- `test_synced_parent_awaited_at_park_stays_guarded`

**1610**, in `tests/hermes_cli/test_kanban_dependency_ordering_sync.py`:

- `test_local_ordering_park_released`
- `test_synced_ordering_park_released_despite_run_id_remap`
- `test_bad_uid_stays_held_even_if_integer_matches`
- `test_foreign_real_uid_stays_held`
- `test_legacy_uidless_checkpoint_only_matches_original_id`
- `test_second_claim_consumes_the_handoff`

These were not rerun by the mirror pass.

Untested:
- Live cross-gateway transport and GitHub lookup; PR state is mocked to `OPEN`.
- End-to-end synced PR comment provenance: the 1610 fixture replaces transported comments with an unattributed publication at the original time, isolating the checkpoint fix from `writer_run_id` remapping.
- CLI, registered tool and Desktop initiation of these follow-up cases; tests exercise database/dispatcher helpers directly.

### Applying

Apply in series order after 1533 and the shared dependency-review checkpoint helper: 1603 before 1610. No new configuration is introduced.

### Gotchas

Added gates are not a bypass for unfinished parents or review evidence. A missing UID permits legacy integer matching, not recovery of an integer remapped by sync. Neither follow-up changes merge authority or grants unlimited continuations.
