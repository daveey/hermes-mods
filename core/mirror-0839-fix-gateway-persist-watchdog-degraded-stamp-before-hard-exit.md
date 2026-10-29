# fix(gateway): persist watchdog degraded stamp before hard-exit [daveey]

Patch 0839, source `1230e3d4af7134e981c63bd62de62fc61ac77775`.

## Behavior

fix(gateway): persist watchdog degraded stamp before hard-exit [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `DEFAULT_LOOP_WATCHDOG_TIMEOUT_S = 10.0`
- `def _mark_exited_quietly(exit_code: int, reason: str) -> None:`
- `def test_loop_liveness_watchdog_marks_runtime_degraded_before_restart():`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> The loop-liveness watchdog waited 0.25s for the background status writer
> and then os._exit, which kills that writer. On a CPU-throttled runner the
> wait expired first, so gateway_state.json stayed "running" after exit 75
> (test_loop_liveness_watchdog_marks_runtime_degraded_before_restart).
>
> Wait on the writer's completion with a 10s bound that returns as soon as
> the snapshot is durable. A wedged filesystem still cannot postpone the
> restart indefinitely. The new test holds persist past the old 0.25s
> deadline so the race is no longer load-dependent.

## Code

- `gateway/shutdown_watchdog.py`

## Tests

Source test files changed by this commit:

- `tests/gateway/test_loop_liveness_watchdog.py`

New or changed test definitions present in the source diff:

- `test_loop_liveness_watchdog_status_stamp_survives_a_slow_persist`
- `test_heartbeat_write_does_not_block_the_loop_it_monitors`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
