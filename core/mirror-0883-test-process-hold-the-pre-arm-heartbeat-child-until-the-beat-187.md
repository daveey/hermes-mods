# test(process): hold the pre-arm heartbeat child until the beat [daveey] (#187)

Patch 0883, source `5fec10c1387433a22fa960768d47b6d4892014b4`.

## Behavior

test(process): hold the pre-arm heartbeat child until the beat [daveey] (#187). This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `train, full suite, deploy) without polling. Invariants: each heartbeat carries o`
- `def test_schema_minimum_heartbeat_is_disabled_for_foreground(monkeypatch):`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Problem: test_first_heartbeat_carries_output_produced_before_arming failed
> attempt 1 with no heartbeat in 10s (test_process_heartbeat.py:129, CI job
> 123158897898) and passed on retry in 3.15s.
> Why 1: nothing was queued, so the wait expired. Evidence: assertion is
> any(type==heartbeat) is False.
> Why 2: _heartbeat_loop only scans _running and skips exited sessions, so
> arming a finished session never emits. Evidence: process_registry due-filter;
> arming an exited session queues only completion.
> Why 3: the fixture is `echo first; sleep 3`. That lifetime is wall-clock and
> keeps running while the runner is descheduled, so "first" is published after
> exit. Evidence: SIGSTOP pytest for 4s once the child exists, child is dead,
> same assertion fails. Retry in 3.15s is the quiet path, where the reader
> wins the race against sleep 3.
> Why 4: the test required a daemon tick during a fixed 3s life, and nothing
> keeps the child alive until that tick.
> Root (happened): success was coupled to a wall-clock child lifetime the tick
> loop does not flush after exit. Widening the 10s wait does not help: the
> event never arrives.
> Root (not caught): the file retry marks the job green, and the failure did
> not say the session had already left _running.
> Recurrence: the baseline fix (e94fc4e26a) held. This is the test added to
> lock it, now racing its own fixture.
> Countermeasures: the child blocks on a fifo until the heartbeat is observed;
> the wait is on that event and returns as soon as it arrives (60s deadline).
> Assert the session is still running before arm. A flush-on-exit heartbeat
> was rejected: completion already carries the tail, and an extra beat is
> another model turn. Heartbeats stop at exit by design.

## Code

- `tests/tools/test_process_heartbeat.py`

## Tests

Source test files changed by this commit:

- `tests/tools/test_process_heartbeat.py`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
