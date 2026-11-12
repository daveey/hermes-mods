# Native gateway turns park at durable core-update boundaries

Patches: **2472, 2473**.

## Behavior

While a core update holds the admission fence and requests a pause, an eligible gateway-owned turn stops at its next completed-tool step boundary instead of counting as active work. Its admission stays the unfinished head of the session FIFO (status `started`, never terminal) and a versioned checkpoint is stored with it. Parked turns are reported separately from active work (`_describe_parked_work`). After the restart or in-place resume the checkpoint is consumed before the turn continues, and the continuation is one synthetic "update resume" event with the shared pause note. It carries no original attachments, platform message id or gateway-control authority, and it has to pass current ingress authorization again.

Only claimed native messaging turns and local-chat turns are eligible. API, finite, producer-admitted automation (`api_turn_v1`, `local_automation_v1`), standalone, cron and managed turns are never parked and keep counting as active work. A user Stop, a new message or a pending steer beats the pause. If the checkpoint cannot be written, the turn is settled as interrupted. If a checkpoint's proof is missing or invalid, the head is fenced as `unknown`, nothing is replayed, and the existing Discard control resolves it.

2473 makes eligibility tolerate a live session whose source has no platform (it is simply not eligible) and fixes test doubles for the new parked-work accessor.

## How it works

`gateway/session_update_pause_hook.py` binds an `UpdatePauseScope` (context variable) and installs a `step_boundary_pause` hook on the cached agent for one run. The hook returns the shared `PAUSE_REASON` only when `admission_fence.pause_requested` is true and no Stop or steer is pending. `gateway/session_update_pause_ingress.py` decides eligibility (`supported`), binds the scope (`pause_scope`), keeps the paused result nonterminal (`retain_pause`) and builds the resume event (`resume_event`). `gateway/session_update_pause.py` runs in the drain after the handler has fully unwound: `park_or_interrupt` writes the checkpoint via `checkpoint_session_input` under the claim gate and mutation lock, `checkpoint_head` recomputes or probes the proof, `resume_checkpoint` consumes it with `resume_session_checkpoint` and rechecks the hold inside the consume transaction, and `refuse_checkpoint` fences an invalid head.

`hermes_state_update_pause.py` stores a one-shot receipt in `state_meta` under `gateway.update_checkpoint.v1.<admission_id>`. The binding is recomputed inside the transaction against admission status, generation, owner epoch, physical session tip, lineage, closed or archived state, live worker states, competing executions and a bounded completed-tool suffix (at most 129 rows, 4 MiB, 8 KiB receipt). Any uncertainty fails closed with `invalid_checkpoint`.

This builds on the TUI-side mechanism in [core-update-capped-pause.md](core-update-capped-pause.md), [update-pause-in-place-handoff.md](update-pause-in-place-handoff.md) and [update-pause-user-stop-wins-2215.md](update-pause-user-stop-wins-2215.md), and shares the fence in [update-admission-fence.md](update-admission-fence.md).

## Code

`gateway/session_update_pause.py`, `gateway/session_update_pause_hook.py`, `gateway/session_update_pause_ingress.py`, `gateway/run_turn_session_info.py`, `gateway/status_planned_stop_pause.py`, `hermes_state_update_pause.py`, plus hooks in `agent/conversation_loop.py`, `agent/turn_finalizer.py`, `gateway/run_turn.py`, `gateway/run_turn_runner.py`, `gateway/run_runtime.py`, `gateway/run_shutdown_restart_wait.py`, `gateway/run_active_work_verb.py`, `gateway/session_authority.py`, `gateway/session_ingress.py`, `gateway/session_runtime_workers.py`, `gateway/status.py`, `gateway/warning_notifications.py`, `hermes_state_runtime.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_state/test_update_checkpoint.py` (checkpoint store and resume), `tests/gateway/test_update_pause_ingress.py` (eligibility and ingress, including the platformless case from 2473), `test_turn_update_pause.py`, `test_turn_update_pause_real_agent.py`, `test_update_pause_composed.py`, `test_update_pause_remaining_guards.py`, `test_update_pause_resume_event.py`, `test_update_pause_resume_prompt.py`, `test_update_pause_scope_identity.py`, `test_update_pause_cache.py`, `test_update_pause_cache_settlement.py`, `test_update_pause_native_boot.py`, `test_update_pause_local_notice.py`, `test_update_pause_pending_followup.py`, `test_update_pause_refusal.py`, `test_authority_update_pause.py`, `test_authority_update_pause_recovery.py`, `test_parked_work_report.py`, all under `tests/gateway/`; test-double updates in `tests/hermes_cli/test_gateway_lifecycle_guards.py`, `test_gateway_tools_notify.py`, `test_shared_metrics_busy.py`. Covered entry path: the gateway turn runner and session authority (native and local turns). Other entry paths (API, cron, managed) are only covered as "not parked" cases in the ingress tests; a live update on a real host was not part of these patches ("separately owned" per the commit message).

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- A parked checkpoint is a scheduling hint plus a recomputed proof, not process quiescence: the owner must stop its in-process executor before parked accounting is published.
- Resuming consumes the proof before running, so a crash after consumption does not replay the checkpoint.
- Resuming is blocked again (`update_pause_held`) if the hold is still in force at the consume transaction.
- A formerly internal wake does not bypass authorization on resume; trusted traits (diagnostic mute, heartbeat owner) are re-stamped privately rather than by marking the event internal.

## Plugin

Core change; no plugin required.
