# Projects tree activity test matches the snapshot shape

Patch **0687** (`a95245bfb9`). Test harness only.

## Behavior

No product change. `test_tree_rows_carry_session_activity_stamps` mocks `_all_counts` with the signature row annotation actually calls: `include_waiting` and `include_snapshot`.

When `include_snapshot` is true the mock returns the nine-tuple `(active, blocked, unseen, remote_stale, waiting, parked, held, stalled, remote_keys)`. `include_waiting` without the snapshot still returns the five-tuple. Neither flag returns the four-tuple counts.

## How it works

The previous mock accepted only `include_waiting`. Annotation now passes `include_snapshot=True`, which TypeError'd the test. The mock grows the extra keyword and the four empty trailing maps.

## Code

`tests/tui_gateway/test_projects_rpc.py` only.

## Tests

The existing `test_tree_rows_carry_session_activity_stamps` is the entry path. This mirror did not re-run it. The product `_all_counts` implementation is untested by this patch.

## Applying

`git am -3` 0687. Test file only.

## Gotchas

- A new mock of `_all_counts` has to accept `include_snapshot` or the tree-activity test fails at call time, not at assertion time.

## plugin?

No.
