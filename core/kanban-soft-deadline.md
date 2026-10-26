# Kanban workers checkpoint and continue instead of dying at max_runtime

Patches:

- `patches/0121-feat-kanban-soft-deadline-kanban_continue-checkpoint.patch`: agent/kanban/tools. Near a card's
  `max_runtime_seconds` the worker is told to checkpoint with a new `kanban_continue` tool. The run ends as
  `continued` and a fresh run picks up from the checkpoint. Long waits can be handed off with a wake time instead of
  a poll loop.

What you see: a long card (a big build, a CI wait, a soak) no longer gets SIGTERMed mid-work at its runtime cap and
counted as a failure. The worker writes down where it is, the card goes back to `ready` (or `scheduled` until a wake
time), and the next run opens with that checkpoint. In stock Hermes, hitting the cap was a `timed_out` run that
lost the run's progress and counted toward the give-up breaker. Most of that time was spent sleeping or polling on CI,
releases and soaks.

## Behavior

- **Soft deadline.** For a card with `max_runtime_seconds`, at `kanban.soft_deadline_fraction` of the cap (default
  `0.85`) the running worker gets a `[Kanban soft deadline]` OUT-OF-BAND steer. A second `FINAL WARNING` steer comes
  two thirds of the way from there to the cap (for a 100 s cap: at 85 s, then 95 s). At most two notices per run.
  `0` disables the soft deadline. Values are clamped to at most `0.99`.
- **`kanban_continue(checkpoint, resume_after_seconds=0, metadata=None)`** (worker-only lifecycle tool). The
  checkpoint is required, redacted, and cut to 12,000 characters. The run closes with outcome `continued`, the claim
  and worker PID are cleared, and a `continued` event is logged. The card goes:
  - with `resume_after_seconds=0`: back to its source phase at once (`ready`, or `review` for a reviewer run);
  - with `resume_after_seconds=N` (1..86400): to `scheduled`. The dispatcher tick moves it back to its phase (or
    to wherever unfinished parents put it) once `N` seconds have passed, and logs `continuation_woken`.
- **Next run's context** starts with a `## Continuation checkpoint (from run N, continuation #k)` section, right after
  the task header and above the transcript tail from 0117. It lists any runs after the checkpoint that did not
  checkpoint again. It is dropped once a later run completes or requests review/changes.
- **Not a failure, not a block.** `consecutive_failures`, `block_kind` and `block_recurrences` are left alone, so
  continuations never trip the give-up breaker or the unblock-loop detector.
- **Own bound.** `kanban.max_continuations` (default `12`) trailing `continued` runs. Hard timeouts, crashes,
  reclaims and rate limits in between do not reset the count; a completed or otherwise-ended run does. Past the
  limit `kanban_continue` refuses and tells the worker to complete or block.
- **Hard kill unchanged.** A worker that ignores the notices is still killed at 100% by `enforce_max_runtime` as
  `timed_out`, which counts toward the breaker. Its retry still gets the 0117 transcript tail.
- **Plain `kanban_schedule` parks are never auto-woken.** Only a `continued` event with a `wake_at` wakes a card.
- **Guidance.** The worker system prompt gains a "Runtime budget" rule: checkpoint near the cap, and for waits over
  ~10 minutes start the job in the background and call `kanban_continue(..., resume_after_seconds=N)`. When a Kanban
  worker starts a background terminal job on a session without async delivery, the `notify_unsupported` note adds
  the same hint and says the job needs `persist_on_release=true` (or nohup) to outlive the run.

## How it works

- `hermes_cli/kanban_continuation.py` (new):
  - `continue_task` checks the card is `running` and still owned by this run (`expected_run_id`), counts the streak
    with `consecutive_continuations`, and enforces the limit. Then, in one write transaction, it updates the task by
    compare-and-set on `current_run_id`, ends the run through `kb._end_run(outcome="continued")` with the checkpoint
    in run metadata, and appends the `continued` event (`wake_at` and `resume_status` in the payload).
  - `wake_due_continuations` scans `scheduled` cards whose latest event is a due `continued` event and promotes them
    via `_landing_status_after_parents`. Counters are not touched; `unblock_task` would reset
    `consecutive_failures`, so it is not used.
  - `render_checkpoint_context` reads the newest `continued` run and builds the context section.
  - `soft_deadline_message` writes the steer text; `soft_deadline_fraction` and `max_consecutive_continuations` read
    the config.
- `tools/kanban_tools.py`: `inject_soft_deadline_from_env(agent)` is called from the activity heartbeat in
  `agent/activity_tracking.py`, next to the comment injection. It polls at most every 15 s. It reads the live
  `max_runtime_seconds` and the active run's `started_at` from the board, so the worker and dispatcher agree on
  elapsed time. It steers the notice and records `(task, run) -> notices sent` in memory. `_handle_continue` is the
  tool handler.
- `continued` is added to `_RUN_OUTCOME_TERMINAL_STATUS` (`kanban_db.py`), so the run's goal loop stops, and to
  `_TERMINAL_KANBAN_TOOLS` (`agent/kanban_stop.py`), so the turn ends after the call.
- The dispatcher's `_run_reclaim_phase` calls `wake_due_continuations` right after `enforce_max_runtime`. It records
  the woken ids in `DispatchResult.woken_continuations` and the tick activity field of the same name.

## Code

- `hermes_cli/kanban_continuation.py`: `continue_task`, `wake_due_continuations`, `render_checkpoint_context`,
  `consecutive_continuations`, `soft_deadline_message`, `soft_deadline_fraction`, `max_consecutive_continuations`.
- `tools/kanban_tools.py`: `inject_soft_deadline_from_env`, `_handle_continue`; `kanban_continue` added to
  `_RUN_LIFECYCLE_TOOLS` and `_TOOLS`.
- `tools/kanban_tools_schemas.py`: `KANBAN_CONTINUE_SCHEMA`.
- `hermes_cli/kanban_db.py`: `build_worker_context` inserts the checkpoint; `continued` outcome;
  `woken_continuations` tick field.
- `hermes_cli/kanban_db_dispatch.py`: `DispatchResult.woken_continuations`; wake call in `_run_reclaim_phase`.
- `agent/activity_tracking.py`, `agent/kanban_stop.py`, `agent/prompt_builder.py` (`KANBAN_GUIDANCE`).
- `agent/transports/hermes_tools_mcp_server.py`: `kanban_continue` in `EXPOSED_TOOLS`, for codex-runtime workers.
- `tools/terminal_tool_background.py`: `_KANBAN_WAIT_HANDOFF_NOTE` in `_apply_async_support`.
- `hermes_cli/config_defaults.py`: `kanban.soft_deadline_fraction: 0.85`, `kanban.max_continuations: 12`.
- `website/docs/user-guide/features/kanban.md`: "Runtime caps: checkpoint and continue".

## Tests

`tests/hermes_cli/test_kanban_continuation.py` (6 pass):

- `test_soft_deadline_checkpoint_requeues_and_next_run_resumes`: no steer at 50%; one steer naming
  `kanban_continue` at 86%, not repeated on the next poll. `_handle_continue` returns `ready`/continuation 1, clears
  the claim, closes the run as `continued` with the checkpoint, logs the event, and the card can be claimed at once.
  The next context has the checkpoint section above the 0117 transcript tail.
- `test_ignoring_soft_deadline_is_hard_killed_and_retry_carries_context`: at 96% of a 100 s cap the steer is the
  `FINAL WARNING`. Past the cap `enforce_max_runtime` kills the worker (`timed_out`, `consecutive_failures` 1), and the
  retry context has the transcript tail but no checkpoint section.
- `test_continuations_do_not_trip_give_up_or_block_loop`: five continuations leave `consecutive_failures=1` and
  `block_recurrences=1`, with no `gave_up`/`block_loop_detected`/`blocked` events. `continue_task` with
  `max_continuations=5` then refuses and the card stays `running`.
- `test_wait_handoff_wakes_on_dispatcher_tick`: `resume_after_seconds=900` parks the card in `scheduled` with
  `wake_at`. `wake_due_continuations` does nothing early and promotes it to `ready` when due (failures still 0, the
  checkpoint in context). A `kanban_schedule` park is never woken.
- `test_continue_refuses_stale_run_and_bad_args`: a blank checkpoint, `resume_after_seconds` over the max, and a
  stale run id are all refused, and the card stays `running`.
- `test_background_notify_note_points_kanban_workers_at_wait_handoff`: the `notify_unsupported` note names
  `resume_after_seconds` only when `HERMES_KANBAN_TASK` is set.

Untested:

- The wake through a real dispatcher tick. The test calls `wake_due_continuations` directly, not
  `_run_reclaim_phase`/`dispatch_once`, so the tick wiring and the `woken_continuations` result field are untested.
- The heartbeat hook in `activity_tracking.py` that calls `inject_soft_deadline_from_env` (tests call it directly).
- Config parsing: the tests stub `soft_deadline_fraction` and pass `max_continuations` explicitly, so reading
  `kanban.soft_deadline_fraction`/`kanban.max_continuations`, the `0` = off path, and the clamp are untested.
- Resuming to `review` for a reviewer run, and landing behind unfinished parents on wake.
- Checkpoint truncation at 12,000 characters.
- The `kanban_stop` turn end after `kanban_continue`, the codex MCP exposure, and the `KANBAN_GUIDANCE` text.

## Applying

0121. Depends on 0117 (`patches/0117-fix-kanban-preserve-worker-ownership-and-retry-conte.patch`, documented in
[kanban-macos-worker-start-time.md](kanban-macos-worker-start-time.md)). 0117 provides the transcript tail that rides
under the checkpoint (`kanban_worker_recovery`, `worker_recovery_sessions`), and the first two tests check for it.
Related to 0113 ([kanban-timed-waits.md](kanban-timed-waits.md)): 0113 sends timed waits to `kanban_schedule`, which
needs a human or cron to wake the card. `kanban_continue(resume_after_seconds=N)` is the self-waking variant for waits
the worker can time.

Verified: applies with `git am -3 --empty=keep` on top of 0001–0120 at merge-base 4957bbd0a0, Oct 17 2026 PT.

## Gotchas

- The soft deadline is a steer, not a guarantee. A worker deep in one long tool call (for example a blocking wait)
  sees it only when the turn next reaches the heartbeat, and may still be hard-killed.
- The next run is a new process. `process_manage` ids do not carry over; background jobs must be started with
  `persist_on_release` or nohup and be re-adopted by PID and log path from the checkpoint.
- The notices-sent map is in worker memory, keyed by `(task, run)`. That is fine because each run is a fresh process.
- `wake_due_continuations` only looks at the card's latest event. Any later event on a scheduled card (a comment, for
  example) hides the `continued` event, and the card is not auto-woken.
- With the default 4 h cap, 12 continuations is about 48 h of runs before the worker must complete or block.

## Plugin?

No. It adds a run outcome to the kanban state machine, a lifecycle tool that needs the run-ownership checks, a
dispatcher tick phase, a section in `build_worker_context`, and a hook in the agent's activity heartbeat. None of
these have plugin hooks.
