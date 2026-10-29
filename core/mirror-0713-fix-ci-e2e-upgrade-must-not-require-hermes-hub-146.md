# fix(ci): [daveey] e2e-upgrade must not require hermes-hub (#146)

Patch 0713, source `cb2e783fc566b03495acdcebf71ec68690ff5bcc`.

## Behavior

fix(ci): [daveey] e2e-upgrade must not require hermes-hub (#146). This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `jobs:`
- `def test_fast_label_is_known_and_checks_cannot_bypass_the_gate():`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> The only ci-bwrap slots are on a spot box. hermes-hub on that box is what
> scheduled long jobs onto hosts EC2 ACPI-stops mid-run. Match ci-bwrap alone.

## Code

- `.github/workflows/tests.yml`

## Tests

Source test files changed by this commit:

- `tests/ci/test_fast_runner_routing.py`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
