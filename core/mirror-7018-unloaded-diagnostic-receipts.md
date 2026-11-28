# Stage-local unloaded-notification diagnostic receipts

Patch coverage: 6596, 6597, 6598, 6599.

## Behavior

The unloaded-chat notification scheduler and the remote-change fanout now record *why* work was admitted, projected, certified or retried, using small fixed metric vocabularies. Previously host-pass and fanout totals mixed admission, projection, certification and health-acknowledgment units, so retry sources and conservative-fallback causes were opaque.

This is intended as observation only (owner statement; the production edit is a restructure of `notice_fanout.py` and the schedule/gate modules, so no-behavior-change rests on the listed suites, not on the diff being additive). The series states no change to eligibility, settlement, cadence, retry interval, cache or wake policy, and keeps earliest-deadline selection, the public list behavior, frozen `Inputs` and legacy fake-report call signatures as they were.

Follow-ups: a completed memo certification is kept independent of a later health-acknowledgment error; old result objects without the optional provenance still work; a remote call exception raised before health entry is no longer labelled a certification exception when projection acquisition had failed first (`not_attempted` is preserved before entry and a finalized certification survives later health failures).

## How it works

`tui_gateway/unloaded_diagnostics.py` holds the stage accounting (a `Stage` context, `stage_accounted`, a `decorate` wrapper and per-stage counters). Host CPU accounting is lexical, so it stays correct across early returns and `BaseException`. The scheduler (`gateway/kanban_notification_schedule.py`) and `gateway/notification_retry_provenance.py` can carry optional retry/deadline provenance (`origin`, `earliest`) without altering which deadline wins. `tui_gateway/notice_fanout.py` / `notice_fanout_result.py` observe `_remote_change_observed` and certification (`_certify_remote`), with the certification interval tracked from projection acknowledgment to its final decision. The schema is described in `docs/designs/unloaded-diagnostics.md` (added by this series).

## Code

- `tui_gateway/unloaded_diagnostics.py`, `tui_gateway/notice_fanout.py`, `tui_gateway/notice_fanout_result.py`
- `tui_gateway/kanban_unloaded.py`, `kanban_unloaded_gates.py`, `kanban_unloaded_schedule.py`, `tui_gateway/notification_facts.py`
- `gateway/kanban_notification_schedule.py`, `gateway/notification_retry_provenance.py`
- `docs/designs/unloaded-diagnostics.md`

## Tests

Read from the source diffs only; **not executed in this mirror run, no pass/fail claimed**.

- `tests/tui_gateway/test_unloaded_diagnostics.py`, `test_unloaded_gate_diagnostics.py`, `test_unloaded_retry_diagnostics.py`, `test_unloaded_diagnostic_contracts.py`, `test_remote_change_diagnostics.py` (scheduler/fanout/gate paths, conservation, CPU partitions, branch provenance, old-call controls, certification-before-health ordering).
- Counts quoted in the source commit messages (suite and consumer-sweep totals) are owner-reported only.
- Entry paths: the remote fanout and the local scheduler are exercised through fixtures; no live host-pass was exercised. Anything outside those suites is **untested** here.

## Applying

Treat ordinals 6596-6599 as one dependency unit, applied in order after the earlier series (patches share files; use `git am -3`, see README Applying).

## Gotchas

- Diagnostics only: do not read the new counters as a policy signal; metric vocabularies are bounded and static.
- Memo certification is not health acknowledgment; the two are reported separately by design.
- Optional provenance defaults must stay absent-safe, otherwise legacy report objects break.

## Why core, not a plugin?

The scheduler and fanout live in core gateway/TUI code with no plugin hook.
