# Kanban claim scope, review handback, and placement explanations

Six follow-ups preserve creator scope, run identity, and existing placement intent.
Sources: **1382** `88e03dbdf9`, **1387** `154de342db`, **1389** `801824d02a`,
**1398** `097b54f5e2`, **1399** `c6acc8f8e4`, **1408** `e3bd1c8cd1`.
See also [managed worker run scope](managed-worker-run-scope.md) and
[authenticated dependency-review continuation](kanban-dependency-review-continuation.md).

## Behavior

### 1382 — Cron fixture cleanup scope
This is test-only: cron attribution does not grant a worker archive authority.
Tool and CLI tests permit cleanup only for its own creator-attributed, non-running fixture;
a nonworker supervisor can archive a completed legacy fixture with no creator task.

### 1387 — Shared enclosing run scope
A submitter in a different run's cgroup is accepted without migration when the gateway
shares that exact cgroup. A different run's scope not shared by the gateway still fails closed.

### 1389 — Completed change-request review hands back the PR
Authenticated review continuation accepts common change-request verdict spellings,
including `REQUEST_CHANGES`, rather than requiring only `changes_required`.
A qualifying completed review permits one author continuation; a later author PR comment
and reclaim do not mint another permission. Wrong-PR or missing-head evidence stays held.

### 1398 — One claim, one run; force-complete parked cards
An attached Board-service database retains capture triggers when another home's config
says nothing about sync. A claim records the lease's run UID even without those triggers,
and adopts an already-pulled placeholder instead of inserting a second run.
CLI `complete --force` can close a scheduled placement-forwarded card without unblocking;
the regression pins an archive request for its placed copy, not confirmed remote archival.

### 1399 — Reader generation recovery ordering
Recovery snapshots handles before reading the live `-shm` identity, avoiding eviction of
a healthy reader registered during the check. The subject describes the old order, not the fix.

### 1408 — Machine-pinned create explanation
Tool create results retain `where: local` for local card creation but add a machine-specific
`why` explaining execution through the Board lease. The log carries the same reason;
this is an explanation change, not a new machine-routing mechanism.

## How it works

### 1382 — Archive authority matrix
The fixtures vary tool/CLI entry, creator attribution (owner/other/missing), and lifecycle
(unclaimed/done/running). Worker identity comes from task/run environment, not `created_by`.

### 1387 — Compare to the gateway's cgroup
After detecting a task/run mismatch, `resolve_run_scope` compares the submitter's path
with `_own_cgroup()`. Equality returns `None` (unchanged spawning); inequality raises
`kanban_scope_mismatch`. A child starts inside the gateway's inherited cgroup.

### 1389 — Bind review evidence to the parked PR set
`_requests_changes` normalizes nonempty metadata verdicts; otherwise it requires
`approved is False` plus a recognized `VERDICT:` summary. `_reviewed_prs` still requires
a completed review run and a full `reviewed_sha`. Explicit PR metadata binds only that PR;
otherwise summary `PR #N` references filter the captured set, or the captured set is used.
`resumes_review` unions that evidence without removing the existing checkpoint guards.

### 1398 — Explicit UID ownership and explicit detach
`stage_claim` clears stale `pending_run_uid` and returns the lease UID to the admission
transaction. `claim_and_open_run` updates a matching task's `pending_sync` placeholder
or inserts with that UID directly. `install_or_remove` distinguishes attached state and
absent settings from explicit disablement or enabled sync with a board excluded.
`complete_task(force=True)` adds `scheduled` to allowed statuses; workflow manual
transitions also permit scheduled-to-done. Existing placement archive forwarding is reused.

### 1399 — Snapshot then compare
`recover` calls `_handles(path)` before `shm_identity(path)` so a concurrently registered
first reader is compared against the identity created by its open, not an earlier absence.

### 1408 — Preserve the pin's reason
`_handle_create` uses `_machine_placement` to replace the rewritten local-placement result.
That helper builds the machine reason, logs it, and returns `where`, `runs_on`, `machine`, `why`.

## Code

### 1382 — Test-only source
`tests/hermes_cli/test_kanban_cron_fixture_cleanup.py`: `board`, `archive`, `status`,
and the two cleanup regressions; no production implementation changes.

### 1387 — Managed scope resolution
`gateway/session_kanban_scope.py`: `_own_cgroup`, `resolve_run_scope`.
`tests/gateway/test_managed_kanban_scope_placement.py`: foreign-scope resolver subprocess probe.

### 1389 — Review evidence
`hermes_cli/kanban_dependency_review.py`: `_CHANGES_VERDICTS`, summary regexes,
`_requests_changes`, `_reviewed_prs` (replaces `_reviewed_pr`), `resumes_review`.

### 1398 — Admission, sync, and completion
`hermes_cli/kanban_admission.py`: `claim_and_open_run`.
`hermes_cli/kanban_db_sync.py`: `stage_claim`, `install_or_remove`, `_attached`, `_explicitly_off`, `_home_hint`.
`hermes_cli/kanban_db.py`: `complete_task`; `hermes_cli/kanban_workflow.py`: `DEFAULT_WORKFLOW`.

### 1399 — Generation recovery
`hermes_cli/kanban_generation.py`: `recover` (only the observation order changes).

### 1408 — Tool placement response
`hermes_cli/kanban_placement.py`: `pinned_machine_why`;
`tools/kanban_tools_placement.py`: `_machine_placement`; `tools/kanban_tools.py`: `_handle_create`.

## Tests

These are source-test assertions read from the patches, **not rerun in this mirror**.
No full suite, live gateway, live remote archive, or deployment is certified here.

- **1382**, `tests/hermes_cli/test_kanban_cron_fixture_cleanup.py`:
  `test_acceptance_cleanup_obeys_creator_and_claim_scope` pins both archive entries across the scope matrix;
  `test_nonworker_supervisor_can_archive_legacy_null_owner_fixture` pins supervisor cleanup on both entries.
- **1387**, `tests/gateway/test_managed_kanban_scope_placement.py`:
  `test_submitter_in_other_runs_scope_fails_closed` updates the mocked mismatch control;
  `test_submitter_inheriting_enclosing_run_scope` uses real systemd scopes for shared/different resolver cases.
  The changed tests resolve scope; a full model-call lifecycle for this exception is **untested** here.
- **1389**, `tests/hermes_cli/test_kanban_dependency_review.py`:
  `test_review_request_changes_hands_pr_back_to_author` pins three evidence shapes and consumed permission;
  `test_review_request_changes_for_other_pr_or_without_head_stays_held` pins five refusal shapes.
  All accepted verdict aliases and metadata-free fallback without a summary PR number are **untested** by these additions.
- **1398**, `tests/hermes_cli/test_kanban_sync_capture_gap.py`:
  `test_a_process_whose_home_says_nothing_about_sync_keeps_an_attached_boards_triggers` pins three absent-setting shapes;
  `test_an_explicit_off_still_detaches_an_attached_board` pins disabled/excluded-board configurations;
  `test_a_claim_without_capture_triggers_keeps_one_run_and_leaves_no_pending_uid` pins lease UID, echo deduplication,
  and a later other-card run insert using a fake Board service;
  `test_a_board_echo_pulled_before_the_claim_txn_is_adopted_as_the_run` pins placeholder adoption.
- **1398**, `tests/hermes_cli/test_kanban_placement.py`:
  `test_refused_and_plain_completion_do_not_forward` now pins non-force scheduled refusal and ordinary local completion;
  `test_cli_force_complete_of_parked_forwarded_card_archives_its_copy` pins CLI done state, archive event,
  pending receipt, and request arguments both before and after unblock. Remote archival confirmation is **untested**.
- **1399**, `tests/hermes_cli/test_kanban_ro_cache.py`:
  `test_recover_does_not_evict_a_reader_registered_during_the_check` injects an open during handle enumeration
  and asserts no reader-cache eviction; this is deterministic interleaving, not a parallel stress test.
- **1408**, `tests/hermes_cli/test_kanban_worker_node.py`:
  `test_tool_create_runs_on_and_placement_to_an_enrolled_machine` pins persisted/returned machine capability,
  exact reason/result/log, and note wording. CLI/REST explanation parity and actual remote execution are **untested**.

## Applying

Apply in published ordinal order: **1382 → 1387 → 1389 → 1398 → 1399 → 1408**, with
all intervening patches in the complete series, not these six as a standalone cherry-pick set.
Use `git am -3 --empty=keep` in a disposable core worktree following the
[README replay recipe](README.md#applying), including its required historical bridge.
Dependencies include the existing creator archive guard, managed scope placement (1372),
authenticated dependency-review continuation (0383/0384 and subsequent changes), Board lease/sync
admission, generation-aware reader handling, and enrolled-machine placement/forwarding infrastructure.
These docs do not certify standalone application or replay of the extended series.

## Gotchas

- A temporary `HERMES_HOME` does not isolate an inherited `HERMES_KANBAN_DB`; isolate both for probes.
- Explicit sync disablement still removes capture triggers; missing config is not that instruction.
- Review repair permission is neither merge approval nor evidence that current-head CI passed.
- Force-completion emits a placement archive request; a pending receipt is not proof of remote deletion.
- `where: local` describes where the card was created, not where its machine-pinned worker executes.
