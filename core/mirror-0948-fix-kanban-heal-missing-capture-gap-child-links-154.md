# fix(kanban): heal missing capture-gap child links [daveey] (#154)

Patch 0948, source `6a72fdf91111de9234cf72ecce54ba904ae32601`.

## Behavior

fix(kanban): heal missing capture-gap child links [daveey] (#154). This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def sync_board(slug: str, path: Path, hub: Hub, gateway: str) -> RoundResult:`
- `def diff(live: sqlite3.Connection, shadow: sqlite3.Connection, gateway: str) ->`
- `def test_rows_written_in_a_capture_gap_are_queued_on_the_next_install(live, kanb`

## Code

- `hermes_cli/kanban_sync_client.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_sync_client.py`

New or changed test definitions present in the source diff:

- `test_missing_child_capture_gap_is_healed_by_a_sync_round`
- `test_missing_child_repair_does_not_duplicate_an_inflight_op`
- `test_missing_child_repair_honors_existing_state`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
