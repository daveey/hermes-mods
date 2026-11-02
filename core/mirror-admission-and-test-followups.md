# Mirror follow-ups: admission, restart and test isolation

## Behavior and code

### Patch 1236: ci(js-tests): admit workspace checks by memory as well as CPU [daveey]

Workspace checks admit units under both CPU and declared memory budgets, reading cgroup limits and running an oversized unit alone.

Source: `af533faba80318f6aec9eb41bc114cbafe2650ec`.

Code and test paths: `.github/scripts/run-workspace-checks.mjs`, `.github/workflows/js-tests.yml`, `apps/desktop/package.json`, `tests-js/run-workspace-checks.test.mjs`.

### Patch 1237: ci(js-tests): run-workspace-checks always runs main; OOM count is per run [daveey]

The workspace-check CLI enters main even through a symlink; OOM observations use the per-run memory.events counter delta.

Source: `7a33a706f5505b2752955e3f8032d8555826b979`.

Code and test paths: `.github/scripts/run-workspace-checks.mjs`, `.github/scripts/workspace-check-admission.mjs`, `tests-js/run-workspace-checks.test.mjs`.

### Patch 1238: fix(kanban): hold claims during gateway drain; scope crash output to current run (#297)

Gateway drain suppresses new Kanban claims, and crash diagnostics are restricted to the current run marker rather than old worker output.

Source: `01a4e743defa83524983aa169d3de558f026defe`.

Code and test paths: `hermes_cli/kanban_db_dispatch.py`, `hermes_cli/kanban_gateway_drain.py`, `hermes_cli/kanban_owner_unavailable.py`, `hermes_cli/kanban_worker_log.py`, `tests/hermes_cli/test_kanban_core_functionality.py`, `tests/hermes_cli/test_kanban_create_skill_validation.py`, `tests/hermes_cli/test_kanban_gateway_drain.py`, `tests/hermes_cli/test_kanban_managed_worker_loss.py`, `tests/hermes_cli/test_kanban_worker_exit_trailer.py`, `tests/hermes_cli/test_kanban_worker_owner_restart.py`.

### Patch 1240: fix(cron): a gate tick clears a failure streak made only of fire/infra refusals [daveey]

A gated cron tick clears only a failure streak consisting entirely of pre-admission fire/infrastructure failures; genuine agent failures remain.

Source: `ba4de177bd4a89aac65a63071ce8bbd1d351ae7f`.

Code and test paths: `cron/jobs.py`, `cron/scheduler_authority.py`, `cron/scheduler_bookkeeping.py`, `tests/cron/test_cron_recover_pause.py`, `tests/cron/test_gated_tick_clears_fire_streak.py`.

### Patch 1241: fix(kanban): respawn guard maps replica comment events to local rows by (created_at, author, len) [daveey]

Replica PR respawn guards map event comments to local rows by timestamp, author and length, failing closed on ambiguous mappings.

Source: `9be462ac4575358ebf58be30d475b7287d699db0`.

Code and test paths: `hermes_cli/kanban_db_dispatch_pr_state.py`, `tests/hermes_cli/test_kanban_pr_replica_comment_ids.py`.

### Patch 1242: fix(gateway): bound core-update waits on managed Kanban runs [daveey]

Core-update shutdown bounds the managed Kanban worker wait while preserving other shutdown waits; cooperative restart stops book an uncounted retry.

Source: `ee48c7b12f64e624ddd01697b7045cf137e9ddee`.

Code and test paths: `gateway/run_shutdown.py`, `gateway/run_shutdown_restart_wait.py`, `gateway/session_kanban_restart.py`, `gateway/session_managed_worker.py`, `gateway/session_runtime_workers.py`, `tests/gateway/test_core_update_kanban_wait.py`, `tests/gateway/test_kanban_restart_stop_booking.py`.

### Patch 1243: fix(kanban): preserve restart disposition through late child exits [daveey]

Late child closure preserves the restart-stop disposition, including unacknowledged children; default gateway drain bound is 75 seconds.

Source: `adc1eb199ffc3b05e39a3ac4f908574be8a78432`.

Code and test paths: `gateway/session_kanban_restart.py`, `gateway/session_managed_worker.py`, `tests/gateway/test_core_update_kanban_wait.py`, `tests/gateway/test_kanban_runtime_stop.py`.

### Patch 1244: fix(gateway): restart-loss booking never blocks admission settlement

A restart-loss booking write failure is logged rather than escaping and preventing admission settlement.

Source: `962f19153a974b240ceeb6f4fc5bf2d1023c304c`.

Code and test paths: `gateway/session_kanban_restart.py`, `gateway/session_managed_worker.py`, `tests/gateway/test_kanban_restart_stop_booking.py`.

### Patch 1245: fix(e2e): align extra-fire oracle and own sandbox gateway teardown [daveey]

The virtual-clock oracle preserves the scheduled interval occurrence after manual extra fire; sandbox teardown signals only its identified live gateway and retains leak checks.

Source: `622f0bd85f69d6b3e58dce792875262b8c56408c`.

Code and test paths: `tests/e2e/core/delivery/test_cron_virtual_clock_soak.py`, `tests/e2e/core/providers/_catalog_helpers.py`, `tests/e2e/core/terminal/_pty.py`, `tests/e2e/core/terminal/test_pty_process_lifecycle.py`, `tests/e2e/core/terminal/test_terminal_transcript_pty.py`.

### Patch 1246: fix(kanban): report removed pinned skills to the worker instead of crashing [daveey]

A removed pinned skill becomes worker recovery guidance rather than a bootstrap crash; catalog tests clean up their sandbox gateway.

Source: `a62d6d73c122798b641c65a8db157dcb83457b47`.

Code and test paths: `gateway/session_kanban.py`, `tests/e2e/core/kanban/test_kanban_worker_contract.py`, `tests/e2e/core/providers/_catalog_helpers.py`, `tests/e2e/core/providers/test_catalog_gateway_lifecycle.py`.

### Patch 1247: test(e2e): retain viewer-exit leak checks and share stale-pin recovery [daveey]

Viewer exit exempts only the identified runtime from leak detection, and stale-pin recovery guidance is shared with the worker path.

Source: `ae4adeb60e8e42f7487a51a356a0672830b75581`.

Code and test paths: `gateway/session_kanban.py`, `hermes_cli/kanban_skill_check.py`, `tests/e2e/core/kanban/test_kanban_worker_contract.py`, `tests/e2e/core/terminal/_pty.py`, `tests/e2e/core/terminal/test_pty_process_lifecycle.py`, `tests/e2e/core/terminal/test_terminal_transcript_pty.py`.

### Patch 1248: test(e2e): seed public fake-model metadata before catalog routing checks [daveey]

Catalog fixtures seed offline public fake-model metadata while retaining the foreign-provider egress sentinel.

Source: `989596b5ca6c3bec3a38e7beb6c56b870438d842`.

Code and test paths: `tests/e2e/core/providers/_catalog_helpers.py`, `tests/e2e/core/providers/test_catalog_metadata_isolation.py`.

### Patch 1249: test(kanban): cover partial and absent pins under owner-frame execution [daveey]

Owner-frame pinned-skill tests cover partial and absent pins and canonical recovery guidance.

Source: `a4f128b916d4e34b85c479374d749a1a0b0b657f`.

Code and test paths: `tests/gateway/test_session_kanban_skill_pins.py`.

### Patch 1250: test(desktop): let Radix's unmount focus timer fire before each file's jsdom closes

Desktop test cleanup yields for the Radix unmount focus timer before a per-file jsdom closes.

Source: `71425f675f9a1bbca361fa65b46d3ef5d7183624`.

Code and test paths: `apps/desktop/vitest.setup.ts`.

### Patch 1251: test(desktop): synchronize preference tests with real bridge completion (#300)

Preference tests synchronize on actual bridge completion rather than fixed scheduler turns.

Source: `7bf24ed230b5f2690f9814010ab9a9fc2cdbd5c3`.

Code and test paths: `apps/desktop/src/lib/ui-preferences/guidance-sync.test.ts`, `apps/desktop/src/lib/ui-preferences/sync-concurrent.test.ts`, `apps/desktop/src/lib/ui-preferences/sync-defaults.test.ts`, `apps/desktop/src/lib/ui-preferences/sync-store.test.ts`, `apps/desktop/src/lib/ui-preferences/sync-test-bridge.ts`.

### Patch 1252: test(plugins): cover bytecode creation between cold hook lookups [daveey] (#286)

Cold plugin-hook lookup tests create bytecode between lookups to verify the metadata fingerprint stays reusable.

Source: `27945f32f185e312a3e08f4748977053410464e2`.

Code and test paths: `tests/hermes_cli/test_plugin_hook_presence_record.py`.

### Patch 1253: test(desktop): isolate hub poller imports and teardown from test deadlines

Hub-poller test imports and teardown are isolated from the test deadline in the session-list action suite.

Source: `2dc64603e1ed4f667baa8293c4345136072ba773`.

Code and test paths: `apps/desktop/src/app/session/hooks/use-session-list-actions.test.tsx`.

## How it works

The patches retain their source diffs and are appended at their first-parent merge position. Patches 1245-1249 are the five branch commits integrated by the tested sync merge; they follow the mainline changes already published before that merge. See the verification report for replay and source-tree comparison.

## Tests

The test paths listed with each patch are its source-owned behavioral evidence. New cases cover memory admission, symlink entry, drain claim suppression/current-run output, cron streak classification, ambiguous replica mappings, managed-worker shutdown/late child exit/write failure, sandbox ownership and leaks, stale pins and offline metadata. The desktop changes update test synchronization, not application behavior. These suites are **not rerun by this mirror task**; no fresh pass count or live behavior is claimed for these follow-ups. The separately documented frozen-decision matrix is the targeted source test run for this publication.

## Applying

Apply the complete numeric series from the inventory merge-base using `git am -3 --empty=keep`. These patches depend on earlier fork work and are not standalone backports.

## Gotchas / plugin boundary

Restart and admission settlement are core-owned, not plugin replacements. Test-only entries change test harnesses and do not imply a deployed runtime fix. Private identities in mail headers and example fixtures are normalized; operational routing identifiers are retained so the series can replay faithfully.

Verification: [mirror-1253-verification.md](mirror-1253-verification.md).

## Follow-up patch (1585): monitor-source and config gate failures get their own kind

Patch: `patches/1585-fix-cron-a-gate-tick-clears-a-streak-made-only-of-mo.patch` (`6ce531f86e`, `fix(cron): a gate tick clears a streak made only of monitor-source/config gate failures`). It extends patch 1240 above.

**Behavior.** A hub cron watcher showed "error, 5 failures in a row" for hours after it recovered. The streak held four monitor-source failures (GitHub HTTP 403) and one owner refusal, followed only by healthy unchanged-monitor ticks. Patch 1240 lets a gate tick heal a fire-only streak. But a monitor-source failure set no kind flag, so it was booked `last_error_kind="agent"` and no later gate tick could clear it; the agent wakes only on a change. Now there is a third kind, `"gate"`, for a monitor-source failure in `_apply_monitor_gate` and an unparseable-config refusal in `_prepare_job_prompt`. The next good gate tick clears a streak made only of fire and gate failures. Any agent failure in the streak keeps the old keep-verdict behaviour. The prompt-injection block refusal stays agent-kind on purpose, because it re-trips on the next real wake.

**How it works.**
- `cron/scheduler.py` sets `job["_gate_error"] = True` on both pre-agent gate failures.
- `gateway/session_cron.py` adds `_gate_error` to `RUN_JOB_FLAGS`, so the flag crosses the owner boundary with the cron result.
- `cron/scheduler_bookkeeping.py` pops it on a failed run and passes `gate_error=True` to `mark_job_run`.
- `cron/jobs.py`: `_PRE_AGENT_ERROR_KINDS = ("fire", "gate")`. `_record_run_outcome` lifts `keep_verdict` for either kind. A failure is booked `"agent"` once any failure in the streak is the agent's own. Otherwise it is booked `"gate"` when it is a gate error or the prior streak kind was `"gate"`, and `"fire"` when the streak is fire-only.

**Code.** `cron/jobs.py`, `cron/scheduler.py`, `cron/scheduler_bookkeeping.py`, `gateway/session_cron.py`.

**Tests.** In the patch; not run for this mirror. The commit reports 8 red before and 11 green after. `tests/cron/test_gated_tick_clears_gate_streak.py`:
- `test_monitor_failures_are_booked_as_gate`
- `test_gate_tick_clears_a_streak_made_only_of_monitor_failures`
- `test_gate_tick_clears_the_live_monitor_then_fire_sequence`: the incident's sequence.
- `test_gate_tick_keeps_a_streak_that_holds_an_agent_failure`
- `test_fire_then_gate_streak_stays_pre_agent`
- `test_real_monitor_script_failure_then_recovery_heals_end_to_end`: a real monitor script fails, then recovers, through the owner path.
- `test_every_pre_agent_failure_return_tags_a_failure_kind` (parametrized over `_apply_monitor_gate`, `_prepare_job_prompt`, `run_canonical_job`): an AST guard that fails on any `return (False, ...)` branch tagging neither `_gate_error` nor `_fire_error`.
- `test_guard_sees_an_untagged_failure_return`: the guard catches a planted untagged return.
- `test_gate_error_rides_the_cron_result_across_the_owner_boundary`: `_gate_error` is in `RUN_JOB_FLAGS`.

**Gotchas.** A new pre-agent failure branch must tag a kind, or the AST guard fails. An injection-block refusal is still booked `"agent"`.
