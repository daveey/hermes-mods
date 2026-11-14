# Normalize crash errors before the fingerprint width limit

Mirrored entries: 3882.

## Behavior

Crashes with the same root error group together even when worker PIDs have different digit widths near the fingerprint cutoff. Different actual exit errors still remain distinct.

## How it works

`error_fingerprint` now substitutes PIDs across the full input before the existing timestamp cleanup and final 80-character lowercased/stripped cut. Previously the raw PID width moved that cut and retained different fragments of the worker’s last-output tail, splitting a systemic failure cohort.

## Code

- `hermes_cli/kanban_db_values.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions in the staged diffs, not fresh execution results. Full-series replay verification belongs to the parent mirror pass.

- `tests/hermes_cli/test_kanban_error_fingerprint_width.py`: Asserts different PID widths yield one fixed-width normalized fingerprint, different exit codes remain distinct and timestamp widths normalize. A multi-card crash sweep spanning the PID-width boundary must auto-block all affected cards and create one systemic fix card.

## Applying

Use the complete ordered series in [README.md](README.md), including its historical integration bridges, ancestry handling and mandatory post-replay hostname escape repair and repaired-tree check. These entries are not independent cherry-picks. Follow the README snapshot-specific policy/replay recipe rather than applying just this document’s subset. This document makes no deployment or live-runtime claim.

## Gotchas

The fingerprint is intentionally lossy and width-limited, not a unique error identifier. This changes grouping and systemic-count behavior without changing the underlying crash receipt. No real crash cohort was processed in this pass.

## Plugin?

No. Dispatcher crash grouping must use one shared core normalization routine.
