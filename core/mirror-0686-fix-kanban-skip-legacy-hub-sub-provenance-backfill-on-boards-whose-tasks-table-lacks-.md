# fix(kanban): skip legacy hub-sub provenance backfill on boards whose tasks table lacks created_at [daveey]

Patch 0686, source `cb3080476313e622058e3c588d873bd797ddb9cf`.

## Behavior

fix(kanban): skip legacy hub-sub provenance backfill on boards whose tasks table lacks created_at [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _migrate_add_optional_columns(conn: sqlite3.Connection) -> None:`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> The board-open backfill joined tasks.created_at, which a legacy/minimal tasks
> table (test_kanban_lockguard's scratch board) does not have, so connect() raised
> OperationalError. Rows stay unstamped there and use the inherit-time fallback.

## Code

- `hermes_cli/kanban_db_connect.py`

## Tests

No test file changes in this source commit. Runtime behavior is untested by this mirror unless an executed check is named in [mirror-958-verification.md](mirror-958-verification.md); full-series application is a structural check, not a behavior test.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
