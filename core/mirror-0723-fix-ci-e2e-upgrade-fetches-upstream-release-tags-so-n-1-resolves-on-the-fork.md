# fix(ci): [daveey] e2e-upgrade fetches upstream release tags so N-1 resolves on the fork

Patch 0723, source `6725b6ff61025ede06fbb8723328f979eab63347`.

## Behavior

fix(ci): [daveey] e2e-upgrade fetches upstream release tags so N-1 resolves on the fork. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `jobs:`
- `def test_e2e_upgrade_runs_only_on_bwrap_capable_slots():`

## Code

- `.github/workflows/tests.yml`

## Tests

Source test files changed by this commit:

- `tests/ci/test_fast_runner_routing.py`

New or changed test definitions present in the source diff:

- `test_e2e_upgrade_has_release_tags_for_n_minus_1`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
