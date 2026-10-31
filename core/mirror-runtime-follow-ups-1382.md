# Mirror runtime follow-ups (1403-1411)

Eight small fixes from one run of the fork: a test deflake, a desktop archive warning, two CI artifact-quota patches, a log-level fix for retired worker generations, a checkpoint size-cap fix, an admission-lock retry, and a sidebar fix. They are independent apart from one pair (1405, 1406).

Related: [archive-linked-confirm.md](archive-linked-confirm.md), [checkpoint-staging-cap.md](checkpoint-staging-cap.md), [background-review-retirement-hold.md](background-review-retirement-hold.md), [desktop-remote-primary-no-local-serve.md](desktop-remote-primary-no-local-serve.md).

## Behavior

- **1403** The OSC 11 "post-deadline straggler" test no longer depends on a 20 ms sleep inside a 50 ms window.
- **1404** Archiving a chat whose sidebar row says it has open Kanban work reads the live task list before warning. A chat whose only "open" cards are settled ones awaiting close-out archives without a prompt. If the live read fails, the row's count is shown.
- **1405** The Windows desktop attach e2e failure-artifact upload can no longer fail the lane (quota errors). Retention drops from 14 days to 1.
- **1406** A CI test pins that no `upload-artifact` step can fail its job, and that nothing behind the required-checks gate downloads an artifact.
- **1407** When a gateway restart or core update retires a worker generation mid-turn, the resulting `stale_generation` refusals log one WARNING instead of an ERROR traceback.
- **1409** A checkpoint take over `checkpoints.max_total_size_mb` drops the oldest snapshot of the current project only, and only once per pending gc. A gc pending for over 48 h logs one warning per process.
- **1410** A briefly busy `admission.lock` (held by another process) no longer refuses a turn: reservations retry for up to 0.5 s. A real update hold or a retiring backend still refuses at once.
- **1411** While the window is on another gateway, the sidebar still lists this Mac's chats, read from the connections-registry primary. A local target is read passively, so a remote-only client never starts a local serve.

## How it works

### 1403 (`20f46da`)
The straggler OSC 11 reply is written from a monkeypatched `termios.tcsetattr`, which runs when terminal state is restored: after the main loop has consumed DA1 and before the drain window opens. No thread, no sleep.

### 1404 (`3ee37f9`)
`refreshArchiveTasks` used to return early for any fresh row. The row's `kanban_active` also counts settled cards waiting for chat close-out, so it can read "7 open" with no open card. Now a fresh row with `kanban_active` or `kanban_blocked` set falls through to the live `/api/session-kanban-tasks` read, which lists only open cards. A fresh row is not marked `kanban_unknown` when that read fails, so its own count stands. A fresh row with zero counts still returns early.

### 1405 (`88a09ef`)
`continue-on-error: true` on the "Upload failure artifacts" step of `windows-desktop-attach-e2e.yml`, and `retention-days: 1`.

### 1406 (`5286331`)
New `tests/ci/test_artifact_quota_advisory.py` parses every workflow with ruamel. One test requires `continue-on-error: true` on every `actions/upload-artifact` step. The other starts at the job named `All required checks pass (v2)` in `ci.yaml`, follows `needs` and local reusable-workflow calls, and fails if any reached job uses `download-artifact`.

### 1407 (`4885a5e`)
`is_retired_generation_error(exc)` matches only `WorkerPersistenceError` or `hermes_state_runtime.RuntimeStoreError` whose message is exactly `stale_generation`. `_guarded_cleanup(..., interrupted=...)` logs a retired-generation error as a one-line WARNING when the turn was interrupted, and still appends it to `cleanup_errors`. Only the `persist_session` step passes `interrupted`. `DurableTurnLease.release` logs a retired-generation refusal as a WARNING, because the owner drops the row itself. Other errors, and the same error on an uninterrupted turn, stay ERROR with traceback.

### 1409 (`5437847`)
`Pruner.drop_oldest_once(ref, cap)` replaces `drop_one_round`. It returns False if the cap is off or `.gc-pending` exists. Otherwise it uses a bounded `rev-list --max-count` probe, checks the store size, and drops the oldest commit of this ref via `_rewrite(..., gc=False)`. `trim()` first runs the same bounded probe. `_mark_gc_pending` now creates the marker with `O_CREAT` and no utime, so its mtime keeps the first deferral. `stale_gc_notice()` reports once per store per process when that age exceeds 48 h, naming `hermes checkpoints prune`. Store-wide round-robin remains in `enforce_size` (periodic prune), unchanged.

### 1410 (`84e2354`)
`admission_lock` raises `AdmissionBusy(TimeoutError)` on timeout. `backend_retirement._reserve` retries in 50 ms tries until `busy_wait_s`, then logs one WARNING without traceback. A hold returns "held" at once; any other error fails closed with traceback. `reservation(busy_wait_s=...)` defaults to `BUSY_WAIT_S = 0.5`, and `begin_background_review` uses the same retry (see [background-review-retirement-hold.md](background-review-retirement-hold.md)). `prompt.admit` passes `busy_wait_s=0` because it holds the session resume lock; warm `session.resume` auto-continue is wrapped in `single_lock_try()` for the same reason. `last_refusal()` and `refusal_text()` let `prompt.submit` and side-agent spawns return 5035 "briefly busy; retry" for the busy case.

### 1411 (`d86463b`)
`ownRecentsConnection(registry, windowPrimary)` returns the registry primary (the window's primary socket only without a registry) and `passive: true` for a local-kind target. The old code compared the window's request connection with `primaryGatewayConnectionId()`. Switching to the hub re-homed that socket, so both named the hub and no local recents were fetched. `SidebarSessionsRequest.passive` is forwarded to `hermesApi`. `useSessionListActions` fetches the second page with these values. Background: [desktop-remote-primary-no-local-serve.md](desktop-remote-primary-no-local-serve.md).

## Code

- 1403: `tests/hermes_cli/test_cli_light_mode.py` (`TestOsc11DrainGuard`).
- 1404: `apps/desktop/src/app/session/hooks/use-session-actions/refresh-archive-tasks.ts` (`refreshArchiveTasks`).
- 1405: `.github/workflows/windows-desktop-attach-e2e.yml`.
- 1406: `tests/ci/test_artifact_quota_advisory.py`.
- 1407: `agent/runtime_session_store.py` (`is_retired_generation_error`), `agent/turn_finalizer.py` (`_guarded_cleanup`, `finalize_turn`), `agent/turn_facade_lease.py` (`DurableTurnLease.release`).
- 1409: `tools/checkpoint_pruning.py` (`Pruner.drop_oldest_once`, `trim`, `_count_at_most`, `gc_pending_age`, `stale_gc_notice`, `_mark_gc_pending`), `tools/checkpoint_manager.py` (prune call site).
- 1410: `hermes_cli/admission_fence.py` (`AdmissionBusy`), `hermes_cli/backend_retirement.py` (`_reserve`, `reservation`, `begin_background_review`, `single_lock_try`, `last_refusal`, `refusal_text`), `tui_gateway/turn_admission.py` (`acquire`, `admission`, `start_work`), `tui_gateway/session_lifecycle.py`, `tui_gateway/methods_prompt.py`, `tui_gateway/methods_prompt_admission.py`, `tui_gateway/session_auto_continue.py`.
- 1411: `apps/desktop/src/app/session/hooks/primary-recents.ts` (`ownRecentsConnection`), `apps/desktop/src/app/session/hooks/use-session-list-actions.ts` (`fetchPage`), `apps/desktop/src/api/sessions.ts` (`listSidebarSessions`, `passive`).

## Tests

These are the source repo's tests, carried in the patches. They were **not rerun in this mirror**; the claims below are what the test code asserts.

- 1403: the straggler test in `test_cli_light_mode.py::TestOsc11DrainGuard` (rewritten; its name is not shown in the diff). It asserts the OSC 11 straggler is swallowed and `_query_osc11_background()` returns None.
- 1404: `confirm-linked-archive.test.ts`: "archives without asking when a fresh row only counts settled cards awaiting close-out" (live read empty, no prompt); "asks with the live count, not the row count" (shows `1 open · 1 blocked`); "keeps a fresh row's count when the live read fails" (shows `2 open`). The commit says the first two fail without the change.
- 1405: untested. It is a workflow-only edit; 1406 covers the `continue-on-error` part only. Nothing asserts the retention value.
- 1406: `test_every_artifact_upload_is_continue_on_error`, `test_required_gate_never_reads_an_artifact`. Coverage is limited to workflows in `.github/workflows` and to `needs` and local `uses: ./.github/workflows/...` edges.
- 1407: `test_turn_finalizer_cleanup_guard.py`: interrupted + `WorkerPersistenceError("stale_generation")` gives one WARNING without traceback and the error still appears in `cleanup_errors`; same for `RuntimeStoreError`; uninterrupted stays ERROR; parametrized other errors (locked DB, `outbox_full`, plain `RuntimeError("stale_generation")`) stay ERROR with traceback. `test_turn_facade_lease.py`: `test_release_refused_by_a_retired_generation_is_a_warning`, `test_release_other_failure_stays_an_error`. Cleanup steps other than `persist_session` are untested.
- 1409: `test_checkpoint_size_cap.py`: `test_snapshot_over_cap_never_reads_or_rewrites_another_projects_history` (no `for-each-ref`, no other project's ref in any git call, other history unchanged); `test_snapshot_does_not_drop_again_while_the_last_drop_awaits_its_gc` (one drop per gc cycle, later `rev-list` calls are bounded probes); `test_gc_pending_for_days_is_reported_once`. The commit says all three fail on the base. Untested: the `trim()` bounded probe as its own path (seen only indirectly), and `O_CREAT` mtime preservation (the stale-notice test sets the mtime by hand).
- 1410: `test_admission_fence.py`: `test_reservation_survives_lock_briefly_busy_without_hold` (200 ms holder, admitted, no warning); `test_reservation_refuses_long_busy_lock_with_one_warning` (2 s holder, refused in 0.45-1.5 s, one warning without traceback, `last_refusal()=="busy"`); `test_hold_refuses_at_once_without_busy_retry`; `test_zero_busy_wait_makes_one_short_try` (also covers `_session_turn_admission(busy_wait_s=0)` and `single_lock_try()`); `test_busy_refusal_text_says_briefly_busy` (5035 text from `_lock_in_submit_turn`, busy vs hold); `test_background_review_survives_briefly_busy_lock`. `test_prompt_admission.py::test_admission_under_resume_lock_makes_one_short_lock_try` pins `busy_wait_s == 0` on `prompt.admit`. Untested: the side-agent 5035 text, and the warm `session.resume` call site beyond the shared `single_lock_try` test.
- 1411: `primary-recents.test.ts` `ownRecentsConnection` (local registry primary is passive; ssh primary is active; no registry falls back to the window primary); `use-session-list-actions.test.tsx`: lists the registry primary's recents when the window's primary socket moved to the hub (second call has `connectionId: 'local', passive: true`; stale `turn_live` gone from the parked row); asks once when the registry primary is the window connection. Untested: `passive` forwarding in `listSidebarSessions` itself, and any Electron-side handling of `passive`.

## Applying

Patches 1403, 1404, 1405, 1406, 1407, 1409, 1410, 1411 (1408 is not part of this set). Apply in numeric order with `git am -3`.

- 1406 depends on 1405: without it the artifact test fails on the Windows attach upload. Apply 1405 first.
- 1404 edits `confirm-linked-archive.test.ts` and `refresh-archive-tasks.ts`, so the archive-confirm patches ([archive-linked-confirm.md](archive-linked-confirm.md)) must be applied first.
- 1411 edits `primary-recents.*` and `use-session-list-actions.*`, so it needs the patches that introduced `withPrimaryRecents` ([desktop-remote-primary-no-local-serve.md](desktop-remote-primary-no-local-serve.md)). It also uses `@/store/connection-registry-state` and `SidebarSessionsRequest.connectionId`.
- 1409 edits `drop_one_round` and `test_checkpoint_size_cap.py`, so it needs the checkpoint pruning patches ([checkpoint-staging-cap.md](checkpoint-staging-cap.md)).
- 1410 edits `begin_background_review`, so it needs [background-review-retirement-hold.md](background-review-retirement-hold.md) and the admission fence patches.
- 1403 and 1407 only need the code they touch to exist.

## Gotchas

- 1404: a failed live read leaves the row's count in the dialog. If the sidebar count is wrong and the read also fails, the warning can still be wrong.
- 1405: the lane no longer fails on quota, but the diagnostic artifact may silently be missing. Retention is 1 day.
- 1406: the gate walk follows `needs` and local reusable-workflow `uses` only. A `download-artifact` reached some other way is not caught.
- 1407: the match is by exception type and exact message, so a reworded fence message goes back to ERROR. Only interrupted turns get the downgrade; an uninterrupted `stale_generation` stays loud on purpose.
- 1409: with `checkpoints.auto_prune` off, no gc runs, the store keeps its size and no further snapshot is dropped on the take path. Run `hermes checkpoints prune` when the warning appears. The cap is now eventually consistent rather than enforced per take.
- 1410: callers holding the session resume lock must pass `busy_wait_s=0` or use `single_lock_try()`; otherwise a take can wait about 0.55 s under that lock. `AdmissionBusy` is a `TimeoutError`, so older `except TimeoutError` handlers still fail closed.
- 1411: if the registry names a remote primary, the read is active and can start that backend. Only a local-kind primary is passive.
