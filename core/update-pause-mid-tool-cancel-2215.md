# Resumable update pause during a running tool

Patches **2162, 2176**. Follow-up to [update-pause-in-place-handoff.md](update-pause-in-place-handoff.md); process survival is described in [terminal-update-survival-2215.md](terminal-update-survival-2215.md).

## Behavior

An auto-continuable Desktop/TUI gateway turn no longer has to wait for a long tool to finish before pausing for an update. After 30 seconds from the later of the pause request's first observation and the newest running tool's start, a watcher requests cancellation. The next iteration uses the normal durable boundary-pause flow so startup recovery can continue the turn.

Cancelled calls receive `[interrupted by Hermes update at <UTC timestamp>: re-run if still needed]`; already finished calls in a parallel step retain their real results. 2176 preserves a valid terminal JSON `continues_in_background` result with a nonempty `session_id` instead of replacing it with the rerun stamp, so the resumed turn can see the surviving job.

## How it works

`prompt_turn` starts/stops the per-turn watcher beside the boundary hook. Tool start, finish and commit hooks maintain in-flight call IDs. `agent.update_pause_cancel` records cancelled calls before publishing an interrupt with reason `hermes update pause`; commit stamps only the recorded calls while that remains the interrupt issuer. The next `begin_iteration` consumes the record, attempts the normal pause, and clears only that interrupt reason under the interrupt publication lock.

No cancellation occurs for non-resumable turns, absent running calls or an already pending interrupt. A user Stop wins, including one arriving during the boundary hook. The update cancel is not a user intervention. A withdrawn request clears its own cancellation path and permits continued work; a later request gets a new grace period. The watcher uses `_thread.start_new_thread`, avoiding inline `threading.Thread` test doubles.

## Code

`agent/{conversation_loop,interrupt_control,tool_executor,turn_iteration_prep,update_pause_cancel}.py`, `tui_gateway/{prompt_turn,update_pause}.py`.

## Tests

**Upstream-supplied regression coverage; not executed in this mirror documentation pass.** `tests/tui_gateway/test_update_pause_mid_tool.py` covers a real agent/executor/session DB turn, sequential and parallel calls, an interrupt-ignoring worker, boundary pause and successor startup recovery. It also covers withdrawn/later requests, newest-call grace, non-resumable turns, stale in-flight removal, watcher lifecycle and user-Stop races.

2176 adds `test_a_foreground_command_handed_to_the_background_keeps_its_result` and `test_any_other_result_of_a_cancelled_call_is_stamped` in that file. `tests/tools/test_update_survivable_processes.py::test_update_pause_cancel_keeps_the_surviving_jobs_result` adds the real-systemd terminal handoff/result case. This is not proof of a live Desktop update cycle.

## Applying

Use the complete ordered replay in [README.md](README.md), including its replay plan and integration bridges. These changes share core executor/interrupt state with other patches and are not independent application units.

## Gotchas

Cancellation is not rollback of side effects. Inspect the persisted result before rerunning work, particularly a surviving background command. Non-resumable turns deliberately retain their running call. The 30-second grace begins at observation/newest-call start, not necessarily the wall-clock time the update was requested.

## Plugin?

No. Durable tool results, interrupt precedence and the turn boundary must agree inside core.
