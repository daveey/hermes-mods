# CI-flake producer delivery via durable self-work receipts

Patches: **4610–4611** (5011 snapshot).

## Behavior

CI flake intake no longer treats create's return value as a task id. Producer-owned declaration, route, identity, and complete manifest freeze before submission. Only authoritative route-bound completed courier receipts count as delivery. Unknown, rejected, and timeout outcomes stay pending with the same snapshot. Product and unopted callers keep legacy behavior.

The courier receipt reader is marked as non-board SQLite (read-only courier hub db) so lockguard does not treat it as a Kanban board.

## How it works

Versioned `selfwork-producer` capabilities/prepare/advance CLI for headless adapters. Lost courier cache after commit replays the same journal receipt. Core CI opts in by explicit repository identity. Docs describe the snapshot and qualified owner contract.

## Code

- `docs/ci-flaky-intake.md`
- `hermes_cli/kanban.py`
- `hermes_cli/kanban_origin_owner.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_selfwork_outbound.py`
- `hermes_cli/kanban_selfwork_producer.py`
- `scripts/ci/flaky_intake.py`
- `scripts/ci/flaky_kanban.py`
- `scripts/ci/flaky_selfwork.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/ci/test_flaky_intake_fairness.py`
- `tests/ci/test_flaky_selfwork.py`
- `tests/hermes_cli/test_kanban_selfwork_producer.py`

## Applying

Use the **full ordered 5011 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

Queue acknowledgements are never delivery. Workers cannot use headless prepare. Foreign envelopes are rejected. No live production submission is claimed here.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
