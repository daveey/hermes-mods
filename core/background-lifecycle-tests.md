# Background lifecycle tests wait for outcomes, not guessed timing

Patches **1527–1528** (test-only).

## Behavior

Runtime behavior is unchanged. The serve curator test waits for its persisted final summary before
asserting completion, and the notification-poller rebuild test allows scheduling gaps while still
requiring every stopped thread to exit.

## How it works

1527 parameterizes normal and 0.5-second delayed report writing. A thread event gates the report;
an asyncio queue carries phase-tagged maintenance observations. Paused and active-turn phases must
not run the curator. The test waits up to 30 seconds for `last_run_summary` to contain
`consolidation off`, then observes another actual ticker iteration to prove the interval prevents
a second run. Cleanup releases the report gate, cancels the ticker and joins report workers.

1528 raises poller joins to 30-second watchdogs (previously three seconds for assertions and one
second for cleanup). Joins still return immediately upon exit. The test keeps assertions that the
old stop event is signalled, the previous thread exits, only the replacement remains and closing
that replacement stops it too.

## Code

No runtime files changed:

- `tests/hermes_cli/test_serve_skill_maintenance.py`
- `tests/tui_gateway/test_tui_gateway_notification_poller.py`

## Tests

The changed source cases are
`test_serve_timer_runs_due_curator_once_and_honors_pause[normal|slow-report]` and
`test_session_rebuild_stops_previous_notification_poller`. Read from the actual diffs, not executed
in this documentation pass. These are lifecycle assertions, not latency measurements or a live
serve/notification delivery certification.

## Applying

Apply in complete mirror order using the [README replay recipe](README.md#applying). 1527 depends
on the shared `tests.waiting.async_wait_until` helper and existing curator/ticker implementation.

## Gotchas

A saved `run_count` precedes the asynchronous final report, so it is not proof that summary writing
finished. A queue poll interval is not an upper bound on OS scheduling, imports, I/O or shutdown
drain. Larger watchdogs retain failure assertions; they do not permit orphan threads.

## Plugin?

No. Both patches modify source regression tests only.
