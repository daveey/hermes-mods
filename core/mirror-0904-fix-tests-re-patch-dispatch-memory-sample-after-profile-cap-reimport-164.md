# fix(tests): re-patch dispatch memory sample after profile-cap reimport [daveey] (#164)

Patch 0904, source `8dca42d3f954707d23c0bff9fc2dc8c2cddc6928`.

## Behavior

fix(tests): re-patch dispatch memory sample after profile-cap reimport [daveey] (#164). This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def isolated_kanban_home_with_profiles(monkeypatch):`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> The per-profile cap fixture drops every hermes_cli module and reimports
> kanban_db. conftest only patches _system_memory_sample on the module
> object that existed before that purge, so the reimported dispatcher
> reads live host memory and flakes when pressure is elevated.
>
> Co-authored-by: hermes <hermes@localhost>

## Code

- `tests/hermes_cli/test_kanban_per_profile_cap.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_per_profile_cap.py`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
