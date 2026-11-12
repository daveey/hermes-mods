# Capacity advisory test pins

Patches: **2535**.

## Behavior

Test-only change; no runtime behavior changes. It adds four tests that kill surviving mutants in the capacity advisory code:

- A recorded capacity source's validity is the 180 s sidebar bound, not longer (`valid_until_ms - observed_at_ms == 180_000`; execution observation `valid_until` is 1180 for an observation at 1000).
- `describe` with no observed restrictions says "current: restriction coverage incomplete; admissible capacity unknown" and never asserts availability.
- A submitter spawn receipt is never treated as process start, even with binding fields: process start and pid stay `None`, claim-to-start is `None`, and the attempt is censored.
- A telemetry gap between two observations counts as unknown time, not dropped (unknown 10 s, observed 20 s, unexplained idle 40 slot-seconds).

## How it works

Plain assertions against `evidence.record`/`read`/`describe` and the `attempts` and `idle_coverage` metrics helpers. No production module changes. See [kanban-capacity-evidence.md](kanban-capacity-evidence.md).

## Code

`tests/hermes_cli/test_kanban_capacity_evidence.py`, `tests/hermes_cli/test_kanban_capacity_metrics.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): the two files listed under Code are themselves the changed tests. They exercise the internal evidence and metrics modules only; no CLI, tool, REST or Desktop path is claimed.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

None; the tests reuse the existing suites' `board` fixture and `event`/`interval` helpers.

## Plugin

Core change; no plugin required.
