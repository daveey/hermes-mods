# fix(kanban): backfill takes an ancestor's reason only from rows written before the copy [daveey]

Patch 0648, source `ede1d69bd11ae97b4c32d40e29735e20029289d6`.

## Behavior

fix(kanban): backfill takes an ancestor's reason only from rows written before the copy [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def stamp_legacy_hub_sub_provenance(conn: sqlite3.Connection) -> int:`
- `def test_backfill_stops_legacy_observer_copies_spreading(kanban_home, edge):`
- `def test_board_open_survives_a_fenced_backfill(kanban_home, monkeypatch):`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> A copy is always written after the row it copies; an ancestor's same-chat row written later is an
> independent shepherd of that ancestor (often a link added afterwards), not the source. Without the
> ordering check ~150 later shepherd rows on the hub's default board were promoted to origin.
> Dry run on board copies now: runners 54 origin / 255 shepherd, default 859 / 1348.

## Code

- `hermes_cli/kanban_db_notify.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_notify_inheritance.py`

New or changed test definitions present in the source diff:

- `test_backfill_ignores_ancestor_rows_written_after_the_row`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
