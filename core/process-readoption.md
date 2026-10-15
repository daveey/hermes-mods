# Background jobs survive a Desktop restart and still wake their chat

Patches:

- `patches/0072-fix-process-Desktop-backend-re-adopts-its-chats-orph.patch`: backend. On startup the Desktop backend
  re-adopts background jobs its chats left running, watches their PIDs, and wakes the chat when one exits. The
  messaging gateway now adopts only its own jobs, so its shutdown never kills a Desktop chat's job.
- `patches/0073-fix-process-Desktop-backend-re-asserts-its-live-jobs.patch`: backend. Every 30 s the backend puts its
  live jobs back into `processes.json` if another Hermes process dropped them.
- `patches/0076-fix-process-only-the-Desktop-backend-re-adopts-chat-.patch`: tests only. Tests for 0077's behavior.
- `patches/0077-fix-process-readopt-only-the-Desktop-backend-adopts-.patch`: backend. Only a Desktop-owned backend
  adopts chat jobs (a `hermes dashboard` no longer does). An exit for a chat that is not open resumes that chat in
  the background so it runs the completion turn.
- `patches/0085-fix-process-checkpoint-a-background-job-s-notify-fla.patch`: backend. A background job's
  notify flags are written to `processes.json` right after spawn, so a restart can't re-adopt it as non-notifying.

What you see: a chat starts a long background job with `notify_on_complete` (a build or a release watcher) and
then Desktop restarts, for example during a Hermes update. In stock Hermes nothing watched that job again. When it
finished, the chat never heard about it and just sat there looking done. With these patches the new backend picks
the job up again. When it exits, the chat gets a completion turn that says the job "exited (status unknown —
re-adopted after restart)", even if you haven't opened that chat since the restart.

## Behavior

- **Who adopts what.** Every Hermes process on a host shares one `processes.json`. An entry belongs to:
  - the **messaging gateway** when its `session_key` contains a colon (`agent:main:telegram:...`) or it has a
    `watcher_platform`;
  - the **Desktop/TUI backend** when its `session_key` is a bare state.db session id;
  - nobody in particular when it has no `session_key` (legacy). The gateway adopts those, as before.
- **Only orphans are adopted.** An entry whose writer process is still alive is left alone (that rule comes from
  0069, which these patches build on).
- **Only the Desktop-owned backend adopts chat jobs** (0077). A launchd `hermes dashboard` runs the same
  `web_server` lifespan but hosts no chats, so it skips adoption.
- **The gateway's shutdown `kill_all` no longer kills a Desktop chat's job**, because the gateway never adopted it.
- **Exit of a re-adopted job.** A re-adopted job has no pipe, so its exit code can't be collected. If it had
  `notify_on_complete`, the chat gets a completion event with `completion_reason="unknown"` and no exit code. The
  agent sees "exited (status unknown — re-adopted after restart)" and "exit code ?". The display text says
  "Exited (status unknown)". A re-adopted job without notify exits silently, as before.
- **A job that died while no backend was running** still produces that completion when the Desktop backend starts.
- **Detection delay.** A detached-PID sweep runs every 30 s, so an exit is noticed up to 30 s late. The sweep thread
  stops once no detached job is left.
- **Chat not open** (0077). The exit is parked for that chat, and the backend resumes the chat in-process (like the
  startup-resume sweep, with `omit_messages`). Its notification poller then picks up the parked event
  within about 2 s and runs the completion turn. If the chat is open, the exit is parked and its poller takes it
  without a resume.
- **Resume refused** (for example, the chat is open in another backend): the exit stays parked until the chat is
  opened in this backend, and a WARNING is logged.
- **Dropped entries come back** (0073). A Hermes process still running pre-update code (a kanban worker or CLI) may
  rewrite `processes.json` with only its own entries. Within 30 s the Desktop backend writes its live jobs back, so a
  later restart can still re-adopt them. Log line: `Re-wrote process checkpoint: another writer dropped N live
  entries`.
- **Notify flag is saved at spawn** (0085). Before, a restart in the window between spawn and the next unrelated
  checkpoint write re-adopted the job as non-notifying, and its exit was dropped. A watch-pattern job that gets
  promoted to `notify_on_complete` (the lifetime hit cap) is also re-checkpointed.

## How it works

- **New module `tools/process_registry_readopt.py`** (0072, extended in 0073 and 0077):
  - `checkpoint_entry_owner(entry)` returns `gateway`, `tui` or `""`. `owner_accepts(owner, entry)` is the filter.
  - `ProcessReadoptMixin`, mixed into `ProcessRegistry`:
    - `_unknown_exit_event` / `_queue_unknown_exit` build and deliver the completion (`exit_code: None`,
      `termination_source: "readopted_after_restart"`, `park_if_unowned: True`).
    - `park_unowned_event` / `take_parked_events` keep a parked-event list (capped at 256).
    - `sweep_detached_once` / `start_detached_sweep` run the `process-detached-sweep` thread
      (`DETACHED_SWEEP_SECONDS = 30`).
    - 0077 adds `on_unknown_exit`, an optional sink. When set, `_queue_unknown_exit` calls it instead of putting the
      event on `completion_queue`. If the sink raises, the event goes to the queue.
  - `recover_tui_orphaned_processes` calls `recover_from_checkpoint(owner="tui")` and starts the sweep.
    `start_tui_orphan_recovery` runs it on a `process-orphan-readopt` thread. 0077 makes it return 0 unless
    `hermes_cli.process_identity.is_desktop_owned_backend()` is true.
  - 0073 adds `start_checkpoint_keeper`, a `process-checkpoint-keeper` thread that calls `reassert_checkpoint` every
    30 s (`CHECKPOINT_KEEPER_SECONDS`).
- **`tools/process_registry_checkpoint.py`**:
  - `recover_from_checkpoint(owner=None)` now runs the whole read-decide-write under `_checkpoint_file_lock()`
    (`_recover_from_checkpoint_locked`), so two backends starting together adopt an orphan once. The adopter writes
    itself as writer, so a later backend sees a live writer and skips.
  - With `owner="tui"`, a recovered session gets `_report_lost_exit = True`. An entry whose PID is already gone gets
    its unknown-exit completion right away if it had `notify_on_complete`.
  - Session construction moved into `_session_from_entry`.
  - 0073: `reassert_checkpoint()` reads the file under the lock and rewrites it only when one of this registry's
    live session ids is missing. It returns whether it rewrote.
- **`tools/process_registry.py`**: `ProcessSession._report_lost_exit` (new field). When a detached session is marked
  exited and it was running, had `_report_lost_exit` and `notify_on_complete`, `_queue_unknown_exit` runs. 0085
  splits `_check_watch_patterns` into a wrapper plus `_scan_watch_patterns`; the wrapper calls `_write_checkpoint()`
  when the scan turned `notify_on_complete` on. The write happens outside the session lock.
- **`tools/process_registry_notifications.py`**: an `"unknown"` entry in `_REASON_STATUS`, a new `_REASON_OUTCOME`
  map, and `format_process_notification` prints `?` for a `None` exit code.
- **`tools/terminal_tool_background.py`** (0085): `spawn_background_process` calls
  `process_registry._write_checkpoint()` after setting `notify_on_complete`, `watch_patterns`,
  `completion_output_chars` or `heartbeat_seconds`.
- **Startup wiring**:
  - `gateway/run_startup.py`: both `recover_from_checkpoint()` calls now pass `owner="gateway"`.
  - `hermes_cli/web_server.py` `_lifespan`: when `is_desktop_owned_backend()`, it calls
    `tui_gateway.server._install_readopted_exit_waker()` and `start_tui_orphan_recovery()`. `start_checkpoint_keeper()`
    runs in every `web_server` lifespan, including `hermes dashboard`.
- **`tui_gateway/session_notifications.py`**:
  - `_notif_handle_event`: an event with `park_if_unowned` that this session does not own is parked instead of
    dropped.
  - `_notif_take_parked` runs in each session's poller loop every `_PARKED_POLL_SECONDS = 2.0` and takes the parked
    events that session owns.
  - 0077: `_readopted_exit_deliver(evt)` is the `on_unknown_exit` sink. It parks the event. If no live session
    matches the key (`_notif_live_session_matches`), it starts a `readopt-wake-*` thread running
    `_wake_chat_for_readopted_exit(key)`. That calls the `session.resume` handler with `omit_messages: True`, first
    in the launch profile, then in each named profile that has a `state.db` (`_readopt_wake_profiles`). It moves to
    the next profile only on error code 4007 (session not found); any other error stops and leaves the event parked.

## Tests

New files:

- `tests/tools/test_process_orphan_readopt_tui.py` (0072, 0073, 0076). Uses real child processes and a temporary
  checkpoint. Covers:
  - owner classification; TUI recovery adopts only bare-id entries; gateway recovery leaves them alone;
  - the gateway's `kill_all` spares a Desktop chat's job;
  - a re-adopted notify job's exit queues one status-unknown completion; a non-notify job exits silently; a gateway
    job keeps its old silent prune;
  - a job that died while no backend ran still notifies;
  - the startup helper sweeps until exit and the sweep thread ends;
  - the adopter stamps itself as writer; four concurrent backends adopt one orphan exactly once;
  - parked events are taken only by their owner;
  - both startup paths pass their owner (checked by reading the source);
  - `reassert_checkpoint` restores a dropped entry and is a no-op otherwise (0073).
  - 0076 adds an autouse fixture that makes the test process look Desktop-owned.
- `tests/tui_gateway/test_readopted_completion_parking.py` (0072, 0076):
  - an unowned re-adopted completion is parked; an ordinary unowned one is still dropped;
  - the owner's poller takes it once and runs it as a completion turn;
  - only a Desktop-owned backend adopts (0077);
  - `on_unknown_exit` gets the event instead of the queue; with no sink it is queued;
  - an exit for a chat that is not open parks it and resumes the chat with `omit_messages`; an open chat is not
    resumed;
  - the wake tries named profiles after "not found", and stops on any other refusal.
- `tests/tools/test_background_checkpoint_flags.py` (0085): notify flag, `completion_output_chars` and watch
  patterns are in the checkpoint at spawn; a plain job stays non-notifying (control); spawn, simulated restart and
  exit produce the unknown-exit event; a watch-to-notify promotion is checkpointed.

Run with the repo runner:

```bash
scripts/run_tests.sh tests/tools/test_process_orphan_readopt_tui.py \
  tests/tui_gateway/test_readopted_completion_parking.py tests/tools/test_background_checkpoint_flags.py
```

I did not re-run these for this doc.

## Applying

Apply in order: 0072, 0073, 0076, 0077, 0085.

- They need 0069 first. It adds the shared-file checkpoint pieces these patches call: `writer_pid` in entries,
  `_writer_alive`, `_checkpoint_file_lock` and `_write_checkpoint_locked`.
- 0076 is tests only, and its new tests fail until 0077 is applied. Apply both or neither.
- 0077's `web_server.py` hunk uses `is_desktop_owned_backend` and `tui_gateway.server`, which the live file already
  imports. The patch does not add those imports.
- Restart the Desktop backend after applying. Jobs started before 0085 may already be checkpointed without their
  notify flag.

## Gotchas

- The exit code of a re-adopted job is never known. The chat only learns that it ended. Check the job's own output
  file or log for the result.
- An exit can be noticed up to 30 s late (sweep interval), plus up to 2 s for the poller.
- `start_checkpoint_keeper` also runs in a `hermes dashboard`. It only rewrites when that process has live jobs of
  its own that are missing from the file, so it is mostly idle there. Whether running it outside Desktop was
  intended is not clear from the diff.
- If a chat is open in a different backend, the wake's `session.resume` is refused and the exit waits, parked in
  memory, until the chat is opened in this backend. Parked events are not persisted: a second restart before then
  loses them.
- The wake searches profiles by name because a checkpoint entry does not record its profile. A session id present
  in two profiles' stores resumes in the first one found (launch profile first, then alphabetical).
- Old Hermes processes (pre-0069 code) can still overwrite the file. 0073 repairs that within 30 s, but a restart
  inside that window loses the job.

## Plugin?

No. The changes live inside `ProcessRegistry` checkpoint recovery, the background spawn path, the gateway and
`web_server` startup code, and the TUI notification poller. None of these has a plugin hook. A plugin could set
`on_unknown_exit`, but it could not change who adopts what, the checkpoint writes, or the poller's drop-or-park rule.
