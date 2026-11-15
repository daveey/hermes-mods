# Workspace source custody and sibling materialization

Mirror ordinals: 3981.

## Behavior

Materialization events identify the current holder and publication grant accurately; new worktrees live beside the registered clone, not inside its unrelated checkout.

## How it works

The event payload uses this gateway as holder, records prev_holder separately and takes repository/commit/revision/source_kind from the snapshot manifest when available, falling back to known custody revision. New destinations are sibling .worktrees/repository-id/task-id paths, retaining occupied-path and other-task reservation checks. Existing holder-reuse and return-home paths are preserved.

## Code

- `hermes_cli/kanban_source_dispatch.py`
- `hermes_cli/kanban_source_resolver.py`

## Tests

- `tests/hermes_cli/test_kanban_source_resolver.py`: Exact branch/SHA and unchanged clone HEAD are checked; ancestor config cannot leak from the clone; existing and reserved destinations refuse without creation.
- `tests/hermes_cli/test_kanban_workspace_source.py`: Materialized/reused event payloads carry current/previous holder, grant metadata and run UID; absent snapshot/custody/manifest cases preserve only known fields with explicit nulls.

Tests were not rerun by this documentation pass. The assertions above describe the staged diffs, not fresh execution results. Series replay and tree verification belong to the parent mirror verification; this document makes no deployment or live-state claim.

## Applying

Follow the full [README series and Applying recipe](README.md), including its historical integration bridges, ancestry-only handling where specified, snapshot-bound policy/replay plan and mandatory post-replay [hostname escape repair](hostname-escape-repair.md). These ordinals are part of that ordered series, not independent cherry-picks. Run the complete replay and repaired-tree verification rather than applying this feature subset alone.

## Gotchas

Sibling layout prevents inheriting the clone's ancestor build config, not every possible system-level config. Do not move or rewrite existing workspaces as part of applying this change.

## Plugin?

No. These changes belong to core-owned state, lifecycle, routing or test contracts; an optional plugin cannot substitute for the ordered core implementation and its regression coverage.
