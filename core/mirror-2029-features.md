# Mirror 2029: checkpoint, refusal, CI and cron recovery follow-ups

Source snapshot: `39115a6b6799f4935ee2263c1f6f5ff34cea4821`. This document covers patches **2019–2029**. Behaviors below are grounded in the source diffs and their regression tests, not a claim that these tests were executed during documentation.

## Behavior

| Patch | Source commit | Change |
| --- | --- | --- |
| 2019 | `0bdfa756c17ab33909914215cd68619900e970b4` | Checkpoint creation refuses when pre-add removal of oversize paths fails; diff and safe-restore planning also refuse failed staging. An index seed is abandoned if excluded paths cannot be dropped. |
| 2020 | `1a5e39d3c5591e559c40e6b36e515f6e72c07a86` | Relay test synchronization waits for all queued replies to reach the connector before checking per-turn status cleanup, including delayed delivery. Test-only. |
| 2021 | `b513e412e96a6ff63b5ed356ac2b154e80446439` | Refused-turn display notices retain validated category and possible-action warnings without putting refused content back into model history. CI install-shape checks use the upstream pnpm workspace layout. |
| 2022 | `bc03fd5dda750b6cc42cc160cc851697059b9c97` | Real stall-probe test waits for three completed write/fsync samples instead of sleeping a fixed 150 ms. Test-only. |
| 2023 | `5f7f6ea9e7f2efbd601e0cbcd5e3729246e85624` | Gateway live refusal notice matches the retained display notice: it does not infer “not processed” from already-cleaned model history. Duplicate trailing side-effect warnings are removed. |
| 2024 | `dc3b1becb61c1ddb28b743b5f5e2a566c33d44da` | Gateway adds the failed-turn notice before the runtime footer, so warning deduplication still sees the end of the body and the footer remains last. |
| 2025 | `82b069021392d39971232815600a8596adb67c5d` | A bounded board request whose socket worker reports a timeout at/after the total deadline raises the same total-budget `TimeoutError` as the caller deadline path. Early socket timeouts and unrelated transport failures retain their original errors. |
| 2026 | `45c0c9a82b02f98c0af52f2559b7bca068ef3a4d` | Nix pnpm dependency-hash checks use `[self-hosted, docker-ci]` and pinned `actions/checkout`, rather than requiring the upstream runner/checkout provider. |
| 2027 | `03665d7a26206470c59ae161a0edee767d5bc2a8` | Both ordinary completion and recovery of already-terminal cron fires retire the matching uncertainty pause before deleting their admission journal. Cleanup failure does not overwrite a booked terminal verdict. |
| 2028 | `f477396273d97183b8a3d7601f8c65d491db4679` | Unresolved fires share a matching uncertainty hold; retiring one journal cannot resume the job while a sibling still records that hold. Preexisting operator pauses and disabled jobs are not claimed as scheduler-owned pauses. |
| 2029 | `39115a6b6799f4935ee2263c1f6f5ff34cea4821` | Tests pin failed/cancelled recovery, missing targets, damaged/disappearing/unrelated siblings, missing admissions and concurrent operator pause boundaries. Test-only. |

## How it works

### Checkpoints and asynchronous tests

`SnapshotMixin._stage_all` checks `_unstage` before `git add -A`: an oversize tracked file left in the index could otherwise be hashed even if a later check refuses the snapshot. `_seed_from_repo_index` abandons an unsafe seed. `CheckpointManager.diff` and `_safe_restore_plan` return `success: false` after failed staging and attempt to reset their private index to the checkpoint ref.

The relay test waits on actual connector send actions, not model entry or a quiet typing window. The stall-probe test resolves its wait from the `running()` callback at sample completion; its existing sample assertions remain.

### Refusal notices and install shapes

`current_turn_ran_actions` checks tool-call/result evidence in the current turn, falling back conservatively to the working set if its index is unusable. HTTP-200 refusal and nonretryable exception paths pass this bit to the shared result builder. `refused_turn_display_notice` rebuilds copy from typed fields instead of trusting arbitrary result text: a category must be 1–80 ASCII alphanumeric, underscore or hyphen characters, and the actions flag must be exactly `True`.

The managed-worker result carries `refusal_category` and `refused_turn_ran_actions`. Gateway, CLI, ACP and TUI retention call sites use the same builder. The source tests exercise the refusal handler, real SQLite retention, managed-worker frame/owner cleanup, gateway delivery and TUI outcome helper; CLI/ACP full entry flows and the exception-path action warning are **not established by these added tests**.

Gateway notice construction uses those fields rather than rescanning cleaned history. Deduplication removes a body suffix only when both body and notice end with the exact warning. Post-turn hooks still receive body plus eligible footer; final delivery adds the notice before that footer. Streaming/already-sent and intentional-silence footer eligibility are unchanged; this batch does not add dedicated tests for those paths.

CI's TUI install-shape lane includes `ui-tui`, `tests-js`, `apps/desktop` and their dependency closures. The runtime-driver lane installs only the production closure of `tests-js`, with scripts disabled. Shape/classifier tests track `pnpm-lock.yaml` and `pnpm-workspace.yaml`; the macOS source-build test parameters now include both.

### Bounded calls, runner routing and cron retirement

`raise_bounded_error` normalizes only `URLError` wrapping `TimeoutError` when `now >= deadline`, preserving the original as the exception cause. This is error normalization, not a larger request budget.

The runner-routing tests scan Linux job declarations, including matrix strategy values, for upstream-only runner labels and reject provider-specific checkout actions in Nix jobs. They are structural tests, not a Nix build.

Cron retirement validates the journal's job/request identity under `_jobs_lock`, conditionally lifts its recorded pause, then unlinks the journal. A pause is liftable only if its timestamp/reason match the current paused job and no sibling journal for that job records the same identity. Repeated unknown observations preserve the original record with `setdefault`. Boolean `True` records a non-owned hold, not permission to resume. Already-firer-finished reconciliation retires bookkeeping only: it does not save/deliver the old result again. See the [cron follow-up](mirror-admission-and-test-followups.md) for recovery controls and limitations.

## Code

Paths are relative to the Hermes source tree, not this documentation repository.

| Patches | Implementation paths |
| --- | --- |
| 2019 | `tools/checkpoint_snapshot.py`, `tools/checkpoint_manager.py` |
| 2020 | `tests/gateway/relay/test_relay_status_anchor_per_turn.py` only |
| 2021 | `agent/turn_failure_copy.py`, `agent/turn_context.py`, `agent/conversation_loop.py`, `agent/turn_truncation.py`, `agent/turn_recovery.py`, `agent/managed_worker.py`; `gateway/run_turn_persistence.py`, `gateway/session_managed_worker.py`; `hermes_cli/cli_chat_turn_mixin.py`, `acp_adapter/server.py`, `tui_gateway/prompt_turn.py`; `.github/workflows/js-tests.yml`, `.github/scripts/check-runtime-driver-imports.mjs` |
| 2022 | `tests-js/workspace-check-stall-probe.test.mjs` only |
| 2023–2024 | `gateway/run_turn_persistence.py`, `gateway/run_turn.py` |
| 2025 | `hermes_cli/kanban_sync_client.py`, `hermes_cli/kanban_sync_client_attempt.py` |
| 2026 | `.github/workflows/nix.yml` |
| 2027–2028 | `cron/scheduler_authority.py`, `cron/scheduler_bookkeeping.py` |
| 2029 | `tests/cron/test_cron_pause_retirement_controls.py`, `tests/cron/test_cron_terminal_pause_retirement.py` only |

## Tests

**Execution status:** the publication pass subsequently ran the eleven Python files listed in [mirror-2029-verification.md](mirror-2029-verification.md) on both source and public replay: **119 passed, 0 failed per tree**, including 22 tests across the three cron files below. JavaScript, macOS and full install-shape commands below remain reproduction instructions, not fresh execution claims. The incoming cron handoff reports **30 tests passed, 12 mutants killed, and four green CI runs**. That broader test selection, mutation harness and CI evidence were not independently reproduced here; no mutation-harness command was supplied, so none is invented.

Source-owned coverage:

- `tests/tools/test_checkpoint_merge_staging.py`: first-removal-only failure for checkpoint, diff and safe-restore entries, bounded object-store size, and failed excluded-path seed removal.
- `tests/gateway/relay/test_relay_status_anchor_per_turn.py`: immediate/delayed queued delivery with per-message heartbeat/status assertions.
- `tests/agent/test_content_policy_refusal_stop_details.py`: safe/unsafe categories, partial/no-tool refusal, real display/model separation, tampered result text and managed-worker frame/owner retention.
- `tests/gateway/test_content_policy_live_notice.py`: live/stored notice parity, malformed categories, TUI outcome copy, unchanged non-refusal behavior, footer-enabled partial refusal and nonmatching body suffix.
- `tests/ci/test_runtime_install_shape.py`, `tests/ci/test_tui_install_shape.py`: pnpm lane parity and frontend selection. `tests/hermes_cli/test_source_build.py`: changed desktop-input parameters (macOS-marked).
- `tests-js/workspace-check-stall-probe.test.mjs`: completed real fsync samples and cleanup.
- `tests/hermes_cli/test_kanban_sync_metrics_budget.py`: deterministic worker-wins deadline ordering, early timeout and reset controls; existing stalled/trickling transport coverage remains.
- `tests/ci/test_fork_runner_routing.py`: all-workflow Linux routing and portable Nix checkout guards.
- `tests/cron/test_cron_terminal_pause_retirement.py`: ordinary tail, failed/cancelled terminal recovery, operator hold preservation, retry after failed lift, repeated unknown identity and conflicting journal.
- `tests/cron/test_cron_shared_uncertainty_pause.py`: both terminal receipt orderings and preexisting disabled job.
- `tests/cron/test_cron_pause_retirement_controls.py`: missing targets, damaged/unrelated/disappearing siblings, missing job/journal and threaded operator pause serialization.

Exact targeted commands, from a dependency-provisioned source checkout at the frozen tip (not from `hermes-mods`):

```sh
scripts/run_tests.sh -j 1 tests/tools/test_checkpoint_merge_staging.py
scripts/run_tests.sh -j 1 tests/gateway/relay/test_relay_status_anchor_per_turn.py
scripts/run_tests.sh -j 1 tests/agent/test_content_policy_refusal_stop_details.py tests/gateway/test_content_policy_live_notice.py
scripts/run_tests.sh -j 1 tests/ci/test_runtime_install_shape.py tests/ci/test_tui_install_shape.py tests/ci/test_fork_runner_routing.py
scripts/run_tests.sh -j 1 tests/hermes_cli/test_kanban_sync_metrics_budget.py
scripts/run_tests.sh -j 1 tests/cron/test_cron_terminal_pause_retirement.py tests/cron/test_cron_shared_uncertainty_pause.py tests/cron/test_cron_pause_retirement_controls.py
pnpm --dir tests-js exec vitest run workspace-check-stall-probe.test.mjs
# Run on macOS; a Linux skip is not execution evidence for this path:
scripts/run_tests.sh -j 1 tests/hermes_cli/test_source_build.py -k test_update_builds_when_a_desktop_input_changed_since_the_prebuilt_commit
```

Install-shape commands from 2021, each in a separate clean disposable source checkout so a fuller install cannot mask a missing dependency:

```sh
# TUI lane:
pnpm install --frozen-lockfile --filter "{./ui-tui}..." --filter "{./tests-js}..." --filter "{./apps/desktop}..."
pnpm --dir tests-js run check
pnpm --dir ui-tui run check
pnpm --dir ui-tui run build
# Runtime-only lane (a different clean checkout):
pnpm install --frozen-lockfile --filter "{./tests-js}..." --prod --ignore-scripts
node .github/scripts/check-runtime-driver-imports.mjs
```

These commands are reproduction instructions, not executed results. They require the source snapshot's pinned JS toolchain and Python test dependencies. A passing structural workflow test does not demonstrate an actual runner assignment, release install or Nix build.

## Applying

Use the complete snapshot-bound replay procedure in [README.md](README.md), with the 2029 policy/replay plan and its required integration bridges. These append-only mail patches depend on earlier fork work and upstream integrations; applying only `2019` through `2029`, or replaying a single unqualified patch glob without the bridges, is not the supported recipe. In particular, apply both cron implementation patches 2027–2028 and boundary tests 2029 together; 2027 alone lacks shared-hold protection. Mirror replay verification is separate from runtime testing and is owned by the mirror publication task, not claimed here.

## Gotchas

- Refused-turn removal from model history does not undo tools or external effects. The warning is conservative evidence of possible actions, not proof of a successful side effect.
- Do not persist raw provider prose as a display category or trust the result's free-text notice across the worker boundary.
- Failed checkpoint staging refuses rather than returning a partial diff/restore plan; it does not repair the underlying Git failure.
- Cron retirement is not a retry of an old fire. A damaged journal can intentionally retain a hold until recovery can safely establish ownership.
- Cron locking retains the preexisting `_jobs_lock` fallback to in-process locking on unavailable/timed-out cross-process locking; these patches do not establish unconditional cross-process serialization.
- A bare `enabled=False` write identical to an already-disabled uncertainty hold leaves no new pause identity and remains ambiguous. Use an explicit operator pause with its own identity; do not interpret these tests as proving detection of indistinguishable writes.
- Historical journal deletion attribution is unproven. The new tests establish current transition behavior, not which actor deleted a past journal.
- Expired one-shot resume may raise `ValueError`; retirement leaves that job paused for the operator. That branch is present in the code but has no dedicated new test in this batch.
- Test synchronization patches 2020/2022 do not change relay or scheduler runtime behavior. Runner labels in 2026 require appropriately provisioned self-hosted CI.

## Plugin

No new plugin or configuration switch. Checkpoint staging, typed refusal results, persistence owners, bounded request errors and cron journal retirement are core responsibilities. Plugins must not implement pause recovery by blindly enabling jobs or replaying terminal results. Post-turn hook input retains the body/footer behavior described above; the added tests do not independently assert the hook payload. CI-only and test-only patches require no runtime plugin activation.
