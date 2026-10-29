# fix(tests): deflake turn-hold, cross-process lease and hosted-room tests; poll long CI runs for flakes [daveey]

Patch 0817, source `9a6270f82577ea558f6fc9679ac77b2312e9aa2a`.

## Behavior

fix(tests): deflake turn-hold, cross-process lease and hosted-room tests; poll long CI runs for flakes [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def try_acquire_active_session(`
- `def _gh(url: str, token: str) -> Any:`
- `def test_annotations_round_trip_through_the_github_poll(monkeypatch):`
- `async def _drain_deferred(runner, timeout=10.0):`
- `async def test_turn_hold_keeps_admission_and_adopts_watermark_fenced_summary(`
- `from pathlib import Path`
- `def test_resolve_max_concurrent_sessions_values():`
- `def test_cross_process_acquire_claims_only_one_last_slot(tmp_path, monkeypatch):`
- `def _admit(`
- `def _runtime(`
- `def test_queued_task_routes_profile_and_credentials_without_overrides(db: Path):`
- `def test_worker_settles_without_any_client_transport(db: Path):`
- `def test_policy_hooks_prepare_and_publish_terminal_idempotently(db: Path):`
- `def test_transport_resolver_selects_member_transport_without_forking_state(`
- `def test_waiting_room_does_not_block_an_independent_room(tmp_path: Path):`
- `def test_bounded_scheduler_eventually_runs_later_room(tmp_path: Path):`
- `def test_oversized_terminal_reply_is_bounded_without_waiting_for_deadline(db: Pa`
- `def test_turn_deadline_stops_exact_attempt_and_publishes_durable_failure(db: Pat`
- `def test_deadline_releases_worker_capacity_for_later_room(tmp_path: Path):`
- `def test_retry_ignores_late_receipt_from_prior_execution_generation(db: Path):`
- `def test_active_recovered_turn_is_never_resubmitted(db: Path):`
- `def test_ambiguous_recovery_remains_indeterminate(db: Path):`
- `def test_post_submit_observation_failure_preserves_recoverable_outcome(db: Path)`
- `def test_cancellation_is_persisted_before_interrupt_and_fences_late_result(`
- `def test_transient_remote_stop_failure_stays_pending_and_retries(db: Path):`
- `def test_completion_wins_a_race_with_unacknowledged_stop(db: Path):`
- `def test_restart_harvests_completion_before_retrying_durable_stop(db: Path):`
- `def test_pending_local_approval_is_reported_with_safe_choices(db: Path):`
- `def test_cancel_never_interrupts_a_newer_task_in_the_same_session(db: Path):`
- `def test_status_reports_room_blocked_on_unresolved_indeterminate_task(db: Path):`
- `def test_authority_loss_stops_terminal_commit(db: Path):`
- `def test_profile_turn_lock_covers_resolve_submit_and_terminal_observation(db: Pa`
- `def test_stop_is_bounded_and_does_not_interrupt_active_turn(db: Path):`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Three files flaked in PR run 40764402730 shard 1 (each reproduced with a
> deterministic injected delay, no host-wide CPU stress):
>
> - active_sessions: every refused claim rewrote the unchanged registry, an
>   fsync'd write under the exclusive lock, so N contenders queued behind N
>   disk flushes; with a 2 s injected fsync the test hit the CI signature
>   (TimeoutExpired after 10 s). Refusals now write only when they pruned
>   (invariant test, red on base). The test uses one cohort hang guard and
>   an event-ordered late contender instead of per-phase 10 s bounds and a
>   2.5 s sleep.
> - turn-hold adoption: asserted elapsed < 5.0 s on the whole handler; a
>   slow but legitimate agent turn (injected 5.5 s) fails it. The hold is
>   now proven by event order (turn returned while the worker still
>   streamed); a hold that waits for the worker still fails.
> - hosted-room runtime: the helper defaulted to a 0.4 s lease TTL on a real
>   clock, so any >0.4 s stall fenced the attempt (injected 0.6 s stall ->
>   indeterminate, the CI timeout). Default is now the production 30 s;
>   positive waits use a 30 s hang guard. Two tests that had already been
>   patched one by one with lease_ttl_seconds=30 use the default.
>
> flaky_kanban --from-github read only the newest 40 completed runs. A
> 37-minute test run completed behind 53 newer runs, so its FLAKY
> annotations never became cards. It now pages through a 6 h creation
> window (regression test red on base).

## Code

- `hermes_cli/active_sessions.py`
- `scripts/ci/flaky_kanban.py`

## Tests

Source test files changed by this commit:

- `tests/ci/test_flaky_kanban.py`
- `tests/gateway/test_session_hygiene_turnhold_adoption.py`
- `tests/hermes_cli/test_active_sessions.py`
- `tests/tui_gateway/test_hosted_room_driver_runtime.py`

New or changed test definitions present in the source diff:

- `test_poll_pages_past_newer_runs_to_reach_a_long_test_run`
- `test_annotate_mode_tolerates_a_missing_report`
- `test_cross_process_acquire_claims_only_one_last_slot`
- `test_refusal_without_prune_leaves_registry_file_untouched`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
