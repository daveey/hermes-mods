# Observe one-second Electron main-loop delays

Patch **2147**.

## Behavior

The Desktop main-process lag watchdog reports timer callbacks delayed by at least one second, closing the previous one-to-two-second diagnostic blind spot. Sub-second jitter remains quiet. This is observation, not a recovery or restart policy.

## How it works

Electron startup retains a one-second sampling cadence but lowers `thresholdMs` from `2_000` to `1_000`. The existing watchdog compares callback time to expected time and rebases the next expected tick after every callback, avoiding repeated reports of the same delay.

New tests read the actual `main.ts` constructor settings rather than merely repeating configuration in a test harness.

## Code

- `apps/desktop/electron/main.ts`: production watchdog threshold.
- `apps/desktop/electron/main-process-lag-watchdog.ts`: existing lag calculation and logging, unchanged by this patch.

## Tests

Source tests inspected; **not executed in this mirror pass**:

- `apps/desktop/electron/main-process-lag-watchdog.test.ts`, suite `main-process watchdog production threshold`: `records %ims of delay, including the one-second blind spot` with delays 1,000, 1,001, 1,999 and 2,000 ms; `keeps sub-second jitter quiet` with 999 ms.

The parameterized case checks that the following healthy callback does not duplicate the report. No live Electron stall was induced here.

## Applying

Use the [core README full applying recipe](README.md#applying), including its pinned replay plan and integration bridges. This patch is not standalone.

## Gotchas

Delay means lateness beyond the expected callback time, not elapsed time since the preceding callback. Logs appear only after the loop resumes; the watchdog cannot write while that loop is blocked. Tray and window behavior are unchanged.

## Could this be a plugin?

A renderer plugin cannot reliably observe Electron main-loop blockage. Configuring the main-process watchdog remains core work.
