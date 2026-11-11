# Restart-resume skips Board lease grants released unused

Patch **2138**.

## Behavior

After a core update restarts the gateway under running cards, each card's next run should restore the interrupted conversation (`restart_resume` event). Previously it silently started fresh whenever a Board lease grant that was refused or lost its CAS (released with `unused_grant: true`) sat between the restart loss and the real next run. The selector now walks back over such grant rows (at most 32) and applies the unchanged owner-restart test to the first other row. The `restart_resume` event records `skipped_unused_grant_runs`.

## How it works

`restart_candidate` in `gateway/session_kanban_resume.py` loads the previous 33 runs and skips a row only if `_is_unused_grant_run` holds: ended, status and outcome `released`, metadata exactly `{"unused_grant": true}`, no error or summary, no `worker_pid` / `last_heartbeat_at` / `claim_lock`, and events limited to the Board's `claimed` (no local `lock`) and a `released` with `unused_grant` true. A crash, timeout, completion, ordinary release, live grant or any row with execution or admission evidence still blocks. The skipped ids travel through the candidate, `plan_resume`, the `plan_failed` fallback and `record_resume_event`.

Root cause recorded in the source: the "immediately preceding run" rule assumed every run row was an execution, and the existing tests claimed on an unsynced board that never produces grant rows.

## Code

- `gateway/session_kanban_resume.py`.

## Tests

**Source regression coverage; not executed in this mirror documentation pass.** Names come from the source diff; no pass/fail result is claimed.

`tests/gateway/test_session_kanban_restart_resume_unused_grant.py` (the commit message cites 45 parametrized cases built through the real lease client, claim gate and board-sync pull path): `test_the_imported_grant_row_has_the_live_shape`, `test_unused_grants_after_a_restart_loss_still_resume_the_exact_transcript`, `test_a_pending_tool_call_still_gets_its_synthetic_result_after_grants`, `test_an_over_threshold_transcript_resumes_and_compacts_after_grants`, `test_profile_and_home_isolation_still_falls_back_behind_grants`, `test_a_grant_row_altered_into_a_started_or_ordinary_run_blocks_the_resume`, `test_execution_or_admission_evidence_on_a_grant_blocks_the_resume`, `test_an_executed_run_between_two_unused_grants_blocks_the_resume`, `test_a_real_run_after_the_grants_blocks_the_resume`, `test_a_grant_that_has_not_ended_yet_blocks_the_resume`, `test_the_skip_is_bounded`, `test_a_card_with_only_unused_grants_has_no_candidate`.

## Applying

Apply ordinal 2138 as part of the snapshot-aware full replay described in the [README](README.md) (full recipe in its Applying section), in published order, not as an independent patch. It extends the restart-resume selector from earlier patches in the series. No claim is made here about live rollout; this page documents source behaviour only.

## Gotchas

- Metadata is matched exactly; a new key the Board adds to unused-grant metadata makes the row non-skippable (fails closed, resume not attempted).
- The 32-row bound means a longer grant/refusal chain between loss and resume falls back to a fresh session.

## Could this be a plugin?

No. It is the dispatcher's resume selector over core `task_runs` and `task_events` rows.
