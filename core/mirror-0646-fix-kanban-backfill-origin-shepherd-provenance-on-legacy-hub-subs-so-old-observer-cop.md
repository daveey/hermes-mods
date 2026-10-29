# fix(kanban): backfill origin/shepherd provenance on legacy hub subs so old observer copies stop spreading [daveey]

Patch 0646, source `65651a3328565a1dbd2751583eee83556c87dace`.

## Behavior

fix(kanban): backfill origin/shepherd provenance on legacy hub subs so old observer copies stop spreading [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _inherit_notify_subs(`
- `def _migrate_add_optional_columns(conn: sqlite3.Connection) -> None:`
- `def remove_notify_sub(`
- `def test_resubscribe_never_downgrades_an_owning_sub(kanban_home):`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Old inheritance wrote each copied sub with the CHILD's creation time, so a copied hermes-hub
> observer on a descendant passed the 120 s origin-binding window and kept spreading. Board open
> now stamps every unstamped 'hub' sub once (stamp_legacy_hub_sub_provenance): a row whose chat
> also sits on a parent or the creator task takes that ancestor's resolved reason, any other row
> is origin when written within the window of its card's creation, else shepherd. A fenced
> (delegated-child) opener skips it; the inherit-time window stays as the fallback.
>
> Dry run on copies of the hub boards: runners 309 rows (92 origin / 217 shepherd; t_82f80db4,
> t_54aa263b, t_f15d182d all shepherd), default 2207 rows (1362 / 845), each under 0.1 s.

## Code

- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_connect.py`
- `hermes_cli/kanban_db_notify.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_notify_inheritance.py`

New or changed test definitions present in the source diff:

- `test_backfill_stops_legacy_observer_copies_spreading`
- `test_backfill_keeps_existing_metadata_and_skips_stamped_rows`
- `test_board_open_survives_a_fenced_backfill`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
