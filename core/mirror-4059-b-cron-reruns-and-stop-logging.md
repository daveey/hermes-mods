# Cron rerun boundaries and expected-stop logging

Patches: 3785, 3824, 3825.

## Behavior

Protects sleep-aware restart reruns and distinguishes requested/shutdown cron stops from unexpected admitted-turn errors. Expected stops still settle the admission as failed; they do not become successful jobs.

## How it works

The restart tests distinguish awake time from wall time, preserve a cut's rerun budget through deferral, and keep deferred original fires and re-planned one-shots out of the pending-rerun category. A held rerun settles at its next natural slot, including the exact boundary.

`gateway.session_cron` recognizes only the scheduler error prefixes `CronRunCancelled: ` and `CronShutdownInterrupted: ` and raises `CronTurnStopped`, carrying `expected_stop = True`. `SessionAuthority` logs that marker at INFO without the generic ERROR traceback; other exceptions retain exception logging. The requested-stop fixture separately pins this gateway's shutdown taking precedence over an existing cancel marker.

## Code

- `gateway/session_authority.py`
- `gateway/session_cron.py`

## Tests

**Tests were not rerun by this documentation pass.** The assertions below describe the staged test diffs, not fresh execution results. Full replay verification is owned by the parent mirror pass; no deployment or live-runtime claim is made.

- `tests/cron/test_restart_rerun.py` (patch 3785).
- `tests/gateway/test_session_cron.py` (patches 3785, 3825).
- `tests/cron/test_requested_stop_logging.py` (patch 3824).

- **3785:** Restart-rerun cases pin awake-versus-asleep classification, a fresh budget for attempts claimed before another rerun, preserved spent budget on deferral, original/replanned/unsaved-slot non-reruns and exact-slot settlement. The gateway case supplies fake owner clocks and requires restart-cut flags with awake 50 and wall 900.
- **3824:** Shutdown wins over a cancel marker with one no-traceback shutdown warning, no generic failure or requested-stop warnings, lifecycle-interrupt last_failure and no failure streak.
- **3825:** Requested/shutdown prefixes still produce failed admission/results without an ERROR traceback; an unrelated error retains ERROR with exception info.

## Applying

Use the complete ordered series and the [README Applying recipe](README.md#applying), including its historical integration bridges and ancestry-only handling where listed. Then run the mandatory snapshot-bound [hostname escape repair](hostname-escape-repair.md) and repaired-tree checks specified there. These ordinals are documentation groupings, **not independent cherry-picks**; do not apply just this subset or a one-glob series that omits bridges. The parent mirror pass must establish replay/tree verification for the final snapshot.

## Gotchas

Error-prefix matching is deliberately narrow, not a search for cancellation prose. The failed admission result and lifecycle-interrupt job state remain meaningful even when the log severity falls. Restart tests do not change scheduling production code in this batch.

## Plugin?

No. Scheduler owner clocks, job markers and authority settlement are core lifecycle responsibilities.
