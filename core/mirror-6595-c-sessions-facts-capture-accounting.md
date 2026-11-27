# Sessions facts-mode CPU accounting, capture timing and collect attribution

**Scope:** Accounting for facts-mode serving and capture children; opt-in timing; collect substeps.

**Source ordinals:** 6464, 6465, 6466, 6554, 6555

## Behavior

- Facts mode accounts serving and capture-child CPU (incl. reaped grandchildren); fallbacks are recorded. Serving reads are accounted as a `route/profiles.<name>.facts` pass whose pending/unavailable 503 is a designed fallback, not an error, and its run count counts reads, not HTTP requests (a cold request reads twice). The capture child is sampled from Linux procfs (`/proc/<pid>/stat`) when the response frame ends, so it is a lower bound that omits interpreter teardown; it is recorded as `sessions.capture.child` (not claimable by any local thread), with a fallback mark when procfs is unreadable and an error mark when the child did not complete.
- Opt-in payload-free timing observations default off (enabled only by `HERMES_SESSIONS_CAPTURE_TIMINGS=1`); each capture logs sequence, serial range, selected/failed counts and start/end/not-before clocks, never payloads, and a missing or filtered log is unmeasured, not a pass. Machine-metrics collection wraps each step in fixed-name inclusive diagnostics (`collect.*`, `collect.census.*`) that overlap the report and must not be added to step/thread CPU; the work performed is unchanged.

## How it works

- `sessions_capture_process.py`, `sessions_fact_routes.py`, `sessions_capture_timing.py`, `machine_metrics.py`.

## Code

Files touched by the series (paths relative to the source tree):

- `hermes_cli/machine_admission_metrics.py`
- `hermes_cli/machine_metrics.py`
- `hermes_cli/machine_report_census.py`
- `hermes_cli/sessions_capture_process.py`
- `hermes_cli/sessions_capture_timing.py`
- `hermes_cli/sessions_fact_routes.py`
- `hermes_cli/sessions_native_source.py`

## Tests

Source test files changed or added by the series:

- `tests/hermes_cli/test_collect_details.py`
- `tests/hermes_cli/test_sessions_capture_process.py`
- `tests/hermes_cli/test_sessions_capture_timing.py`
- `tests/hermes_cli/test_sessions_fact_accounting.py`

These were read as part of the mirror from the source diffs only. **Source test coverage was NOT executed in this mirror run**; no pass/fail result is claimed.

## Applying

Treat this series (source ordinals 6464-6555, 5 commits: 6464, 6465, 6466, 6554, 6555) as one dependency unit: later commits fix, harden or test earlier ones, so apply them together and in ordinal order rather than cherry-picking. Series-level ordering and the overall mirror procedure are described in [README.md](README.md). Applying means mirroring source into a checkout; it does not install, activate or deploy anything.

## Gotchas

- Timing is off by default.
- Timing switch is read once at construction; logger failure cannot affect capture; filtered/gapped evidence is unmeasured. An unreadable procfs records a fallback, ready facts reads are not fallbacks.
- The collect split adds fixed diagnostics only (no caches, cadence or payload changes); performance acceptance is not claimed.
- Nothing in this document is a deployment or live-behaviour claim.

## Why core, not a plugin?

Built into the web server.
