# A core-update-paused turn waits for a successor backend only while one is coming

Patch: [`patches/0704-fix-core-update-a-paused-turn-continues-in-place-onc.patch`](patches/0704-fix-core-update-a-paused-turn-continues-in-place-onc.patch)
(fork commit `e96d22b072`), [`patches/0712-daveey-Report-background-jobs-interrupted-by-core-up.patch`](patches/0712-daveey-Report-background-jobs-interrupted-by-core-up.patch)
(fork commit `549b7f1e71`, owned background jobs stopped for the pause), and [`patches/0717-daveey-Report-background-jobs-an-idle-chat-loses-whe.patch`](patches/0717-daveey-Report-background-jobs-an-idle-chat-loses-whe.patch)
(fork commit `91f48a91a2`, idle-chat jobs reported when the backend exits)

## Why

A drain window pauses auto-continuable Desktop turns at a step boundary (`tui_gateway/update_pause.py`), and
the update then applies. If the update moves the checkout, the paused turn's own backend leaves it to the
next backend, which continues it from its startup sweep. But that backend's code-skew watchdog
(`hermes_cli/web_server_skew_exit.py`) retires it only through the retirement fence's idle proof. A backend
kept busy by other chats never becomes idle, and a backend with no watchdog is never retired at all. The
in-place watcher still waited its full `LEAVE_GRACE_S` (900 s) before continuing the turn itself.

On David-MBP on 2026-10-28, three paused turns on serve pid 74105 logged "checkout moved" at 07:03:53. That
serve outlived the 07:02:50 update. The turns logged "backend still not replaced after 900s" at 07:18:55, and
session bef6c6 resumed at 07:19:28, a 19.5 min stall.

## Behavior

Once the checkout has moved, the watcher (`start_in_place_watcher`) checks `replacement_outlook()` on every
poll. The poll interval is 5 s.

- **`coming`**: a successor is on its way, so the turn is left to it. This means the retirement fence is
  preparing or holds a permit (`RetirementFence.retiring()`), or the skew watchdog is running
  (`skew_watchdog_armed()`) and `idle_proof()` reports idle.
- **`none`**: no skew watchdog runs in this process. The turn continues here on the next poll.
- **`blocked`**: the watchdog runs, but other work, a pending prompt, an unreadable ledger or a live update
  marker keeps the backend up. After `NO_REPLACEMENT_S` (60 s) of this with no `coming` in between, the turn
  continues here.
- **Backstop**: after `LEAVE_GRACE_S` (now 300 s), the turn continues here anyway.

The window-ended-without-an-update path is unchanged. The skew check still sends the turn to `leave` first,
and a backend that is retiring never continues the turn itself.

A turn that has already moved on reports `done` before the skew check, so it is never handed off or resumed
again. "Moved on" means it is running, already scheduled (`_auto_continue_scheduled`), closing or finalized,
or its marker is no longer a pause.

The in-place continuation and the successor's continuation are exclusive. The in-place continuation is
admitted through the retirement fence (`turn_admission.start_work`), so it is refused once retirement prepares
or commits, and the paused marker then stays for the successor's startup sweep. While the in-place continuation
runs, the session is busy, so the idle proof refuses retirement.

## Tests

`tests/tui_gateway/test_update_pause_recovery.py` runs the real watcher thread on a virtual clock:

- `test_no_replacement_coming_continues_in_place_within_the_bound`: the checkout moved and the backend is kept
  busy, and the turn continues within 70 s. It fails on the old code at 905 s.
- `test_no_retirement_watchdog_continues_in_place_at_once`: no watchdog, and the turn continues on the first
  poll.
- `test_skew_with_a_replacement_coming_does_not_continue_in_place`: the backend is skewed, idle and has a live
  watchdog, and no in-place continuation happens.
- `test_requested_replacement_hands_off_and_the_successor_continues_once`: the fence commits retirement. No
  in-place continuation happens even past every bound, and the successor's startup sweep continues the turn
  exactly once.

`tests/hermes_cli/test_web_server_skew_exit.py::test_watchdog_reports_armed_while_polling_and_not_after_it_stops`
covers `skew_watchdog_armed()` and `RetirementFence.retiring()`. `tests/tui_gateway/test_update_pause.py::test_in_place_resume_decision`
covers the decision order.

Not covered by a test: a live drain window where the paused turn's serve survives the update.

## Second attempt (fork `26691e1ce5`, patch [`0725`](patches/0725-fix-core-update-a-paused-turn-continues-within-90-s-.patch))

The first live sample failed acceptance. On David-MBP, 2026-10-28 PDT, serve 13052 survived the update.
Session 2b256c paused at 17:17:53, and the watcher logged "checkout moved" at 17:22:19. A kanban notice
was accepted at 17:22:21, and the turn continued only from the 300 s backstop at 17:30:39, 502 s after the
gateway was back.

Why:
1. The hand-off wait left the paused session idle and claimable. A notice turn claimed it 2 s after the
   move (`_notif_claim_turn` had no notion of a pending continuation) and ran over the pause for 8 min.
   While it ran, the backend was busy, so no retirement could come either.
2. While that carried turn ran, the watcher only waited. The outlook timer was then restarted by the
   flips after it, so only the backstop could fire.

Behavior now:
- **Automatic turns wait for the paused turn.** From the end of the window until the continuation records
  its marker, `_notif_claim_turn` refuses automatic turns, and every caller requeues them: kanban notices,
  process completions, `/loop`, heartbeats and wake-ups. `update_pause.continuation_due` reads the window live
  while a watcher runs, so a notice between two polls is caught.
  - The hold is bounded three ways: `DUE_HOLD_S` past the watcher's last poll; `KICKOFF_HOLD_S` (135 s) after
    scheduling, which covers the agent build; and `HOLD_MAX_S` (305 s) past the window's end, so a
    continuation that keeps failing to schedule can't starve the chat.
- **Every bound counts from the first sight of the move.** That includes a synthesized turn already running
  over the pause, which is the new `carried` action. A flipping outlook can't restart the bound.
- **`coming` is capped.** An outlook that says `coming` but never begins retiring (`RetirementFence.retiring()`)
  continues here after `HANDOFF_WAIT_S` (90 s), which is two skew-watchdog polls plus a margin. `blocked` still
  continues at 60 s. `retiring` keeps the hand-off, and only the backstop retries then, because the fence refuses
  in-place work.
- **Each outlook change is logged** ("replacement outlook for session … is …"), so the next live window shows
  the decision path.

Tests (`tests/tui_gateway/test_update_pause_recovery.py`, real watcher thread on a virtual clock). The first four were run against the first-attempt code and fail there; the fifth needs the new hold API:
- `test_replacement_outlook_that_never_retires_continues_within_the_hand_off_bound`
- `test_fluctuating_outlook_counts_from_the_move_not_from_the_last_flip`
- `test_automatic_turn_waits_for_the_paused_turn_once_the_window_is_over` (the live sequence: a notice right after the move is refused; continuation within 100 s; hold lapses after `KICKOFF_HOLD_S`)
- `test_synthesized_turn_over_the_pause_then_continues_at_once` (a carried 480 s turn; the paused turn continues on the first poll after it)
- `test_a_continuation_that_never_schedules_stops_holding_automatic_turns`

`test_skew_with_a_replacement_coming_does_not_continue_in_place` now bounds the hand-off at `HANDOFF_WAIT_S`.

Not covered: a `/goal` continuation claims the session directly, so it bypasses the gate and can overwrite the
paused marker after a carried turn. This was there before the fix. Follow-up card: hub `t_01a18533d0a0182d`.

## Follow-up patch (0712)

**0712** (fork `549b7f1e71`): a permitted core-update pause stops owned background jobs and says so. It does not try to keep them alive across the update.

At the step boundary, before any signal, the pause journals each owned running job that is not `persist_on_release`: redacted session id, command head (80 chars), start time, notify flag, and `kill_state=pending`. It then calls `kill_process(source="core_update_pause", consume_output=False)` and journals each outcome before the next kill. `core_update_pause` is a lifecycle kill source, so `persist_on_release` jobs are still skipped. A lifecycle kill logs one INFO line: `lifecycle kill session=<chat> process=<id> source=<source>`. An operator kill does not. The completion notice for a notified job says `killed by the Hermes update`.

The resume note lists each stopped job with its id, start, and command head, and says to restart it if still needed. A non-killed outcome says to inspect before restarting. A notified job's completion text is included. Continuation retries keep `killed_jobs` on the marker (`record_turn_start` / `mark_turn_paused(increment=False)`).

If the journal write fails, nothing is killed and the pause is refused. If a kill fails, or the write after a signal fails, the pause is refused and the marker keeps the uncertain receipt. Session-end `agent_close` still kills owned jobs and still skips persist jobs.

Tests:

- `tests/tui_gateway/test_update_pause_killed_jobs.py`: `test_update_pause_records_killed_jobs_and_resume_note_lists_them`, `test_agent_close_still_kills_owned_jobs_and_skips_persist`, `test_lifecycle_kill_is_logged_with_session_process_and_source`, `test_operator_kill_is_not_a_lifecycle_log`.
- `tests/tui_gateway/test_update_pause_failures.py`: `test_write_failure_does_not_kill_or_release`, `test_kill_failure_refuses_pause_and_retains_intent`, `test_receipt_and_normal_notice_are_redacted_and_name_update`, `test_retry_marker_retains_interruption_receipts`, `test_write_after_signal_failure_leaves_uncertain_receipt`.

A live scheduler submitting a real background job, and a real drain window, are untested in this patch. The tests drive the registry and the marker directly.

## Follow-up patch (0717)

**0717** (fork `91f48a91a2`): an idle chat's background jobs are reported when the backend exits. The step-boundary pause never sees them, because the turn already ended.

Skew retirement, the updater restart, idle exit and a Desktop quit all end in `_shutdown_sessions` then `agent.close`, which killed owned jobs with `source=agent_close` and `consume_output=True`. The notify the chat waited on never arrived, and nothing recorded why.

Before sessions are closed, `tui_gateway/exit_job_receipts.py` journals each session's owned non-persist running jobs into its turn marker, stops them with `source=backend_shutdown` and `consume_output=False`, and journals each outcome. `backend_shutdown` is a lifecycle kill source, so `persist_on_release` jobs are still skipped. The journal runs from `_shutdown_sessions` (after turns are stopped) and from the SIGTERM flush handler.

- A turn already in flight keeps its crash marker and gains the job list.
- An idle session gets a `background_jobs_stopped` marker only when one of the jobs had `notify=true`. Quiet-only jobs get no wake marker; the ordinary close still applies.
- If the journal write fails, that session's jobs are not killed by this path and a warning is logged. A failed kill is logged as an unverified outcome. Unlike the update pause, backend exit does not abort the shutdown.
- The kill reason is the update's reason when the checkout moved under this process or an update is live, otherwise `killed when the Hermes backend shut down`.
- The next backend's startup sweep and `session.resume` submit a turn listing each job (id, UTC start, command head, restart advice) and, for a notify job, its completion notice. The status line is `Reporting stopped background jobs…`. The receipt outlives the turn freshness window (`JOBS_RECEIPT_MAX_AGE_S`, 24 h).
- A real session end does not come through this path and still kills owned jobs without a receipt.

Tests in `tests/tui_gateway/test_shutdown_job_receipts.py`:

- `test_idle_shutdown_for_update_journals_notify_job_and_next_backend_reports_it`
- `test_idle_shutdown_without_update_names_the_backend_shutdown`
- `test_idle_session_with_only_quiet_jobs_gets_no_wake_marker`
- `test_running_turn_at_shutdown_keeps_its_crash_marker_and_lists_the_jobs`
- `test_persist_on_release_job_is_untouched_by_shutdown_journal`
- `test_session_end_close_keeps_todays_silent_kill`
- `test_sigterm_flush_handler_journals_before_chaining`
- `test_startup_sweep_resumes_a_job_receipt_older_than_the_turn_freshness_window`

A live scheduler submitting a real background job, and a real backend exit, are untested in this patch. The tests drive the registry and the marker directly.

## Follow-up patch (0719)

**0719** (fork `64e81da1a0`): a synthesized notification turn that carries an inherited core-update-paused marker re-arms the in-place watcher exactly once whether it succeeds, raises, or is refused during input preparation. The original paused prompt remains available to continue when the session becomes idle.

`_rearm_carried_pause_watcher` in `tui_gateway/prompt_turn.py` runs in the turn's innermost `finally`, after post-turn housekeeping. A turn that recorded its own marker does not use this carried-pause path. `_paused_marker_carried` remains set until a later turn records its own marker, so marker-retirement paths do not erase the paused request.

Tests in `tests/tui_gateway/test_update_pause_superseded.py`:

- `test_fresh_backend_notification_turn_success_rearms_the_watcher`
- `test_fresh_backend_notification_turn_that_raises_still_rearms_the_watcher`
- `test_fresh_backend_notification_turn_refused_at_prepare_still_rearms_the_watcher`

These drive `_notif_run_submit` with a fresh session and a marker inherited from the previous backend. They assert the original prompt survives, the watcher is armed once, the session is idle, and the resume decision is `resume`. Watcher startup is captured by a fixture; these tests do not execute a live update or the resumed agent turn.

Applying: append 0719 in series order. It depends on the earlier carried-marker support already in the fork; it is not a standalone replacement for 0704.

## Follow-up patch (0728)

**0728** (fork `9b77743e86`, patch [`0728`](patches/0728-fix-update-pause-a-goal-continuation-waits-for-a-cor.patch)): a /goal continuation no longer runs ahead of a core-update-paused turn. `_run_post_turn_followups` claims the session directly for the goal tick (not through `_notif_claim_turn`, so the 0725 `continuation_due` gate never saw it), and the continuation is a real turn, not a synthesized one. After a notice turn carried the paused marker, the goal tick recorded its own marker over it and retired it, so the paused request was replaced instead of continued.

`_goal_waits_for_paused_turn` in `tui_gateway/prompt_turn.py` drops the goal tick while `_paused_marker_carried` or `_update_pause_marker_key` is set, or `update_pause.continuation_due(session)` holds. The paused turn's continuation (or the user's next message) ends with its own goal evaluation, so the goal resumes after it. Marking the continuation synthesized instead was rejected: it would keep the marker but still run before the paused turn.

Tests in `tests/tui_gateway/test_update_pause_superseded.py`:

- `test_goal_continuation_after_a_carried_notice_turn_keeps_the_paused_turn` (RED before: the goal prompt ran and the marker was gone)
- `test_goal_continuation_waits_while_a_paused_turn_continuation_is_due` (RED before)
- `test_goal_continuation_still_runs_once_the_pause_is_retired` (control: a typed message retires the pause and the goal still continues)

The goal judge is stubbed; no live update or real goal evaluation runs in these tests.

Applying: append 0728 after 0725.
