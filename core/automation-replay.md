# One delayed replay for failed automation turns

Patch **1515**.

## Behavior

- Failed `kanban_notice` and `process_complete` turns get one delayed replay, including failures
  classified non-retryable at the provider-call layer. This is separate from ordinary user-turn retry.
- The failed-turn boundary says what could not be processed and when it will retry, not “send it
  again.” A failed replay says it gave up. Partial-action warnings remain attached.
- The delay is at least 60 seconds, extended to a later recorded `failure_resets_at`. Multiple
  failures in a chat coalesce into one pending timer and a combined, marked replay payload.
- A later successful turn cancels pending deliveries already persisted in its transcript. An
  automation wake that failed during agent build was not persisted and remains pending.
- Owner/sweep/closeout wakes whose build failed use the same one-replay boundary. A completed failed
  build is restarted on a later wake rather than rereading its stale error. Closeout metadata survives.
- A busy chat is rechecked at 60-second intervals, at most ten rechecks; a replay failure does not
  schedule another replay. User prompts retain their separate refused-turn recovery.

## How it works

`tui_gateway.automation_replay` owns per-session entries under a lock. `automation_replay` display
metadata marks the replay. `prompt_turn` installs a temporary failed-turn-copy hook, plans/schedules
error results and exceptions, cancels carried deliveries after success, then removes the hook.
`conversation_loop` accepts the host copy while retaining redaction and partial-action cautions.
`session_notifications` schedules unpersisted failed wakes; `session_registry` preserves numeric
quota reset evidence from build failures.

## Code

- `tui_gateway/automation_replay.py`
- `tui_gateway/prompt_turn.py`, `session_notifications.py`, `session_registry.py`
- `agent/conversation_loop.py`

## Tests

Source tests, not executed in this documentation pass:

- `tests/agent/test_automation_failed_turn_copy.py`: replacement copy; absent/raising override keeps
  ordinary user copy; actions already run still get the partial-action warning.
- `tests/tui_gateway/test_automation_replay.py`: minimum/reset deadline, non-retryable failure,
  coalescing, marker preservation, success cancellation excluding unpersisted deliveries, bounded
  busy rechecks and disabled scheduling.
- `tests/tui_gateway/test_refused_turn_retry.py`: both automation kinds replay once, second refusal
  leaves no timer, later success cancels, repeated refusals cannot loop, recorded quota reset is used,
  user-turn recovery stays separate, owner/sweep/closeout build failures retry and stale builds restart.
- `tests/tui_gateway/conftest.py` disables/resets timers for isolation. No live Desktop rendering or
  backend-restart replay durability was exercised here.

## Applying

Apply the complete ordered mirror using the [README replay recipe](README.md#applying), not 1515
alone. The patch integrates with existing failed-turn boundaries, wake admission and retry ownership.

## Gotchas

Timers are process-local: a backend restart drops pending entries; boot sweeps and notice guarantees
own that case. This is not a durable retry queue. Replay text asks the agent to check current state
before repeating work; it does not make side effects idempotent. A busy recheck is not another model turn.

## Plugin?

Core owns the failed-turn boundary and wake/retry lifecycle. Plugins producing Kanban notices can
use the existing display kind but do not install this feature separately.
