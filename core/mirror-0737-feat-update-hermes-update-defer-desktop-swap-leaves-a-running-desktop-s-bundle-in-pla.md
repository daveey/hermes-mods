# feat(update): HERMES_UPDATE_DEFER_DESKTOP_SWAP leaves a running Desktop's bundle in place [daveey]

Patch 0737, source `a82898b665881c30e832d7b6c2db1e50ab8ca49c`.

## Behavior

feat(update): HERMES_UPDATE_DEFER_DESKTOP_SWAP leaves a running Desktop's bundle in place [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def build_update_products(project_root: Path, *, desktop: bool) -> None:`
- `def _build_desktop_product(project_root: Path, env: dict, publish_stage) -> None`
- `def test_update_swaps_in_a_prebuilt_from_an_ancestor_with_unchanged_desktop_inpu`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> hermes-sync sets it for updates applied while Desktop is open, so the swap no longer stops and
> relaunches the UI under the user; it swaps the bundle on the next quit or while the user is away.
> Unset = upstream behaviour. t_01a1825a82e472f6

## Code

- `hermes_cli/source_build.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_source_build.py`

New or changed test definitions present in the source diff:

- `test_defer_desktop_swap_leaves_a_running_desktop_alone`
- `test_defer_desktop_swap_still_installs_when_desktop_is_not_running`
- `test_without_the_defer_flag_a_running_desktop_is_swapped_as_upstream`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
