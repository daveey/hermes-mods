# Kanban PR unblock recovery

Patch: `0115`, commit `38bf5650b5`.

## Behavior
An explicit unblock after the latest PR comment releases the active-PR respawn
guard, allowing the worker to resume verification/shipping. Crash/reclaim does
not release the guard, and a newer PR comment guards again.

## How it works / Code
`hermes_cli/kanban_db_dispatch.py` checks unblock events alongside handoffs when
deciding whether a PR comment still prevents dispatch.

## Tests
`tests/hermes_cli/test_kanban_review_lifecycle.py` contains the regression tests
added by the source commit. This documentation-only mirror did not rerun them.

## Applying
Apply the ordered patch series through 0115 at the documented fork merge-base.

## Gotchas
An unblock is a resume signal, not evidence that the PR merged or checks passed.

## Plugin?
Core dispatcher state evaluation; no separate plugin tool is involved.
