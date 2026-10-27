# Non-CI retention observation slice

Patch **0391** (`16c20ad165`, core PR #82).

## Behavior

`python -m hermes_cli.retention_cli observe --workspace /abs/path --candidate rel/path` prints a bounded JSON observation of candidate cache directories. It retains every record and cannot delete or authorize deletion. Exit 0 = complete observation, 2 = retained with incomplete coverage, 1 = invalid input.

## How it works

`hermes_cli/retention_plan.py` scans with entry/depth limits and fails closed on symlinks, Git metadata (including bare/reftable repositories), mount boundaries and unavailable descriptors. Byte counts are allocated bytes; partial scans expose measured partial bytes and leave totals unknown. Paths are base64 bytes; hardlinks are deduplicated; overlapping candidate roots are rejected.

## Code

- `hermes_cli/retention_plan.py`, `hermes_cli/retention_cli.py`
- `docs/retention-observation.md` (upstream-of-patch design note with outstanding work)

## Tests

`tests/hermes_cli/test_retention_plan.py` and `test_retention_cli.py`. On the applied public series on macOS most cases skip (Linux fdinfo mount IDs required); see [mirror-392-verification.md](mirror-392-verification.md) for counts.

## Applying

Part of the ordered series (0391); apply the full series from the documented merge base.

## Gotchas

Apply has a code-level refusal and no enable switch. Scans are not atomic and cannot prove rebuildability. No `hermes maintenance` command or completion hook is installed.

## Plugin?

No: it is a staged core slice for a future supervisor-run retention service.
