# Kanban workspace_source: source attach and pre-spawn abort at launch

Patches: **3586, 3587, 3588, 3591, 3595, 3597**.

## Behavior
- With `kanban.workspace_source.enabled` on, the dispatcher's claim asks the Board for custody cards, resolves the source, and attaches the run to custody **before** the worker process starts. The worker environment carries `HERMES_KANBAN_SOURCE_COMMIT`, `HERMES_KANBAN_SOURCE_PATH` and `HERMES_KANBAN_SOURCE_BRANCH`, and a `workspace_source_materialized` event records where it ran. (Env vars and event name are not asserted by name in the listed tests.)
- `source_protocol` is requested per dispatcher call only; grants made outside the dispatcher's claim never carry it.
- A legacy worktree card is adopted by its creator on the first enabled run; a legacy card created by another creator is not adopted. On an old Board that does not know the source actions, the run proceeds without custody, and a card with replica custody is skipped.
- If attach is refused (or admission refuses after attach) the unused grant is released and the worker is never spawned. If no process ever started and the worktree is unchanged, the run is released once with an `unchanged` receipt; a changed worktree is `retained`; a post-PID failure kills the worker and sends `retained`. A spawn error of unknown kind is not trusted as "not started".
- A follow-up review that re-blocks reaches triage and keeps the review resume; a follow-up that fails forever is quarantined with evidence; an empty follow-up drain opens no write transaction; a source card is refused at resolve time when the feature is disabled on the worker.

## How it works
- 3586 adjusts `kanban_source_refusal_block.py` and `kanban_source_dispatch.py` for review re-block recurrence, follow-up quarantine, the no-op drain transaction and the worker-side `enabled` gate.
- 3587 adds `kanban_source_board.py` (synchronous Board source actions with a refusal exception and an old-Board exception, per-call `source_protocol`, a lease-renewal fence, release with receipt) and `kanban_source_launch.py` (observe worktree, attach, pre-spawn abort, release with outbox fallback, launch). It rewires `kanban_db_dispatch.py`, the admission module, `kanban_db_sync.py` and `kanban_sync_client.py`.
- 3588 is a small health/footgun fix in the launch module; 3591 makes legacy adopt read `created_on` from the row and adds the launch tests plus a shared fixture file.
- 3595 consolidates the shared git-world test fixture and lint fixes.
- 3597 shares the failure-command DDL in `kanban_failure_release.py`, fixes the ls-remote batch environment, charges the preflight budget after origin proof, and adds tests for the real admitted-claim path.

## Code
- `hermes_cli/kanban_source_board.py`
- `hermes_cli/kanban_source_launch.py`
- `hermes_cli/kanban_source_dispatch.py`
- `hermes_cli/kanban_source_refusal_block.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_db_dispatch_admission.py`
- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_workspace_source.py`
- `hermes_cli/kanban_failure_release.py`
- `hermes_cli/kanban_source_cli.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_source_launch.py`: `test_source_attach_precedes_spawn_with_no_renew_between`, `test_grant_carries_source_protocol_only_from_dispatcher`, `test_attach_refused_releases_unused_grant_and_never_spawns`, `test_legacy_adopt_by_creator`, `test_legacy_card_of_another_creator_is_not_adopted`, `test_legacy_adopt_on_old_board_runs_without_custody`, `test_pre_spawn_abort_single_release`, `test_pre_spawn_abort_with_changed_worktree_is_retained`, `test_pre_spawn_abort_outbox_fallback_carries_receipt`, `test_post_pid_failure_kills_worker_and_sends_retained`, `test_spawn_error_of_unknown_kind_is_not_trusted_as_not_started`, `test_old_board_skips_card_with_replica_custody`, `test_snapshot_present_null_is_no_custody`, `test_dispatcher_admitted_claim_acquires_with_source_protocol`, `test_admission_refusal_after_attach_is_a_pre_spawn_abort`, `test_old_board_attach_400_on_source_card_never_spawns`.
- `tests/hermes_cli/test_kanban_workspace_source.py`: `test_followup_review_reblock_reaches_triage_and_keeps_review_resume`, `test_followup_failing_forever_is_quarantined_with_evidence`, `test_followup_drain_opens_no_write_txn_when_empty`, `test_resolve_refuses_source_card_when_disabled`.
- 3588 has no tests of its own; 3595 only refactors test fixtures.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Off by default; every machine enabling it needs a core with the full protocol and a Board that knows the source actions.
- A spawn error of unknown kind is treated as possibly started, so the worker is killed before release.
