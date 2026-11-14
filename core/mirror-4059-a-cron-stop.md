# Requested cron stops are warnings, not failed-job defects

Mirror ordinals: 3771.

## Behavior

- A marked operator-requested cron stop logs one warning without traceback rather than the ordinary failed-job error.
- Gateway shutdown keeps its distinct expected-stop warning.
- A genuine lost claim with no cancellation marker still logs a failure with traceback.

## How it works

`cron.cancellation.requested_cancel` exposes a cancellation request through combined events even when loss of the execution claim latched first. `CronRunCancelled` distinguishes that marked stop. The scheduler resolves the request reason before choosing an exception class, gives shutdown precedence, and `_log_expected_stop` handles only the two expected exception types. Ordinary RuntimeError paths retain existing error logging. Compatibility fixtures are updated for the renamed logging helper and typed stop exception.

## Code

- `cron/cancellation.py`
- `cron/scheduler.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions present in the staged diffs, not fresh execution results.

- `tests/cron/test_requested_stop_logging.py` — Real job/run setup with a fake turn triggers remove, cancellation and Stop-style marker cases; each returns unsuccessful execution with its reason but one no-traceback warning and no failed-job ERROR. Unmarked lost ownership still produces one traceback ERROR.
- `tests/cron/test_shutdown_interrupt.py` — Shutdown is recognized as expected and ordinary exceptions are not misclassified by the renamed helper.
- `tests/gateway/test_session_cron.py` — The gateway session-cron cancellation expectation uses CronRunCancelled, distinguishing requested stop from generic runtime failure.

## Applying

Use the full ordered mirror series and the **Applying** recipe in [README.md](README.md), including every historical bridge/replay boundary and the mandatory snapshot-bound hostname escape repair plus its repaired-tree check. These ordinals are not independent cherry-picks. This document does not establish replay success, deployment, or live activation; the parent mirror verification supplies replay evidence separately.

## Gotchas

A requested cancellation is not a successful job. Marker-based classification avoids treating every ownership loss as benign; shutdown is still a separate interrupted outcome.

## Plugin?

No. These changes belong to core lifecycle, persistence, or admission boundaries; a plugin cannot reliably replace those internal invariants.
