# Updater: recover killed HEAD locks and escalate repeated ref-lock failures

Mirror snapshot 5011; ordinals 4441, 4442.

Entries:

- **4441**: Recover killed update HEAD locks and escalate repeated ref lock failures
- **4442**: Preserve updater import closure and ref-lock metric schema

## Behavior

- Receipt ownership and live-holder proofs are kept for `HEAD.lock` recovery of locks left by a killed process. Ref-lock failures are classified separately, their generation is recorded in update receipts, and one idempotent diagnostic card is filed after two distinct failed runs.
- 4442 fixes consumers: ref-lock diagnostics are preloaded before the checkout swap (import closure), the new failure token appears in the shipped shared-metrics v4 schema, and empty ledgers are not written for unrelated receipts.

## How it works

`gitlock.py`, `update_cmd.py`, `update_cmd_commit.py`, `update_receipt.py`, `update_ref_lock_alert.py` and shared-metrics contract/class modules plus the schema.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/gitlock.py`
- `hermes_cli/observability/schemas/hermes.shared_metrics.v4.schema.json`
- `hermes_cli/observability/shared_metrics_contract.py`
- `hermes_cli/observability/shared_metrics_update_classes.py`
- `hermes_cli/update_cmd.py`
- `hermes_cli/update_cmd_commit.py`
- `hermes_cli/update_receipt.py`
- `hermes_cli/update_ref_lock_alert.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here):

- `tests/hermes_cli/test_update_ref_lock_alert.py`
- `tests/hermes_cli/test_update_ref_locks.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered patch list (every segment, in order) with `git am -3 --empty=keep`, followed by any repair overlay that README names for the segment. They are not independent patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- Producer attribution of the original stale lock remained unproven (mtime/reclaim correlation only).
- One Windows-only test is skipped.
