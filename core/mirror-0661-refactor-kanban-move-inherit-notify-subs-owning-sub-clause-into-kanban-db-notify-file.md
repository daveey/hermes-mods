# refactor(kanban): move _inherit_notify_subs/_owning_sub_clause into kanban_db_notify (file-size ratchet) [daveey]

Patch 0661, source `f9c89086425daaa43266184e29f0b988a3def3a4`.

## Behavior

refactor(kanban): move _inherit_notify_subs/_owning_sub_clause into kanban_db_notify (file-size ratchet) [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _missing_task_ids(conn: sqlite3.Connection, ids: Iterable[str]) -> list[str]`
- `def current_run_started_ats(conn: sqlite3.Connection, task_ids: Iterable[str]) -`
- `def rewind_notify_cursor(`

## Code

- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_notify.py`

## Tests

No test file changes in this source commit. Runtime behavior is untested by this mirror unless an executed check is named in [mirror-958-verification.md](mirror-958-verification.md); full-series application is a structural check, not a behavior test.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
