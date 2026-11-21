# Recover update-held prompts with fenced queue custody

Mirror entries: 5104, 5105, 5106.

## Behavior

- Update-held prompts retain idle retry chains across a transient admission/retirement refusal instead of being stranded.
- Startup observation redrives owned queues before code-skew deferral and can hand off orphaned or permanently retired queue custody, even while the old writer process is alive.
- Stop/discard, newer user activity, profile isolation and canonical/alias identity fencing prevent unwanted replay; a cancelled live runtime retains custody until teardown can safely hand it off.

## How it works

`admission_retry.py` rechecks queue state before timer drains and reschedules while valid idle custody remains. Its sweep inventories queues under resume/session locks and matches marker and canonical session keys within the correct profile home. `RetirementFence.retired()` distinguishes permanent committed retirement from a reversible prepare.

Turn markers record explicit queue release only for this process's unchanged update-held marker, fencing reason, writer and start identity. Released queue custody is eligible for startup recovery without pretending the process died. Explicit discard retires only the matching queued marker. Queued markers are not live-running outcome evidence; newer user rows suppress superseded replay. A once-per-head/hold-lift warning identifies a queue still unadmitted after sixty seconds. Cancellation is never reset merely to force replay.

## Code

Paths below refer to the mirrored core source tree, not standalone plugin files.

- `hermes_cli/backend_retirement.py`
- `hermes_state_outcome.py`
- `tui_gateway/admission_retry.py`
- `tui_gateway/session_auto_continue.py`
- `tui_gateway/startup_resume.py`
- `tui_gateway/turn_marker.py`
- `tui_gateway/session_lifecycle.py`
- `tui_gateway/methods_session_interrupt.py`

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

Update-queue recovery tests cover refused-then-successful retries, live-writer orphan handoff, permanent retirement, marker identity/idempotence, idle-owned versus running/foreign queues, changing inventory during reads, alias directions, profile isolation, newer user rows, explicit discard, timer state rechecks and warning reset. Hold-submit fixtures cover ordinary non-Stop gestures and unreachable compute-host cancellation. The cancellation follow-up pins closing/finalized handoff while a live runtime still owns custody. Actual backend update/restart, live Desktop Stop gestures and remote compute cancellation are untested here.

Source test/coverage paths:

- `tests/tui_gateway/test_update_queue_recovery.py`
- `tests/tui_gateway/test_update_hold_submit_queue.py`

## Applying

Apply these entries only as part of the **whole ordered 5406 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches, execute source tests or activate runtime behavior.

## Gotchas

A live writer PID is not sufficient evidence of a live in-memory queue, but a cancelled runtime can still own one until teardown. Never release another profile's or alias owner's marker. Ordinary non-update queues retain their existing retirement refusal behavior. Recovery is not permission to replay discarded or superseded user intent.

## Why core instead of a plugin

Queue custody crosses core admission timers, retirement fences, turn markers, startup resume and interrupt handling. A plugin cannot atomically coordinate those identities or safely revive a prompt after Stop.
