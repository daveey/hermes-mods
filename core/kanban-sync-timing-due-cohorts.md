# Component-evidenced due report overlap

Patch **2005** (`221510a25b32673c6f678f383ce15201b6f1c444`) extends the [bounded sync timing analyzer](kanban-sync-timing-diagnostics.md).

## Behavior

The offline analyzer keeps the existing total report overlap fields and cohorts unchanged. It additionally emits per-served-change `due_report_overlap_ns`, `due_report_coverage`, and `due_report_overlap`, `no_due_report_overlap`, `due_report_overlap_residual_ms` cohorts.

A report is due only when its record includes a collect or send component. Failed, timed-out and raised reports with those components count; a tiny skipped call does not become due merely because it has a positive duration. The residual subtracts that change interval's own measured due-report overlap, not a counterfactual baseline.

## How it works

`_is_due` checks component evidence. `_measure` builds separate whole-report span lists for all reports and due reports; `_served` measures both overlaps. `_reports` uses the same due classifier. Existing schema/provenance validation remains in force.

## Code

- `hermes_cli/kanban_sync_timing_report.py`: `_is_due`, `_served`, `_measure`, `_reports`.
- `tests/hermes_cli/test_kanban_sync_timing_report.py`: six parametrized scenarios.

## Tests

`test_due_overlap_uses_actual_components_and_keeps_total_cohorts` exercises the analyzer's `main` entry with generated captures for skipped, absent, sent, timed-out, raised and send-only reports. It checks total overlap compatibility, due coverage/counts and residuals. The mirror's executed results are in [mirror-2005-verification.md](mirror-2005-verification.md). A standalone `python -m` subprocess and live capture are untested by this mirror pass.

## Applying

Apply the complete [README recipe](README.md#applying) through 2005. This patch depends on 2003–2004; it is not a standalone instrumentation implementation.

## Gotchas

Due coverage is not latency acceptance. There is no matched legacy baseline and no PASS verdict or rollout authorization. This changes only offline analysis; diagnostics remain off by default with the existing 600-second cap. Whole due-report intervals, not just component durations, define overlap.

## Plugin boundary

This extends the core diagnostic analyzer because it consumes the core capture schema. It adds no plugin, runtime activation or scheduler policy change.
