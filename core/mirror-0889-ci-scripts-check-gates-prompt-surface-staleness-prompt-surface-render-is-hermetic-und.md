# ci: scripts/check gates prompt-surface staleness; prompt_surface render is hermetic under any TMPDIR

Patch 0889, source `1cd314df0b296497e7ec8610f332eae310fd1ab7`.

## Behavior

ci: scripts/check gates prompt-surface staleness; prompt_surface render is hermetic under any TMPDIR. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `#!/usr/bin/env python3`
- `_GUARDS = {`
- `both through the production code into tests/fixtures/prompt_surface/, which`
- `def _pin_environment() -> None:`
- `def render(out: Path) -> int:`
- `def main(argv: list[str] | None = None) -> int:`
- `+"""prompt_surface.render gives the same bytes wherever the caller's TMPDIR lives.`

## Code

- `scripts/check`
- `scripts/ci/prompt_surface.py`

## Tests

Source test files changed by this commit:

- `tests/ci/test_prompt_surface_hermetic.py`

New or changed test definitions present in the source diff:

- `test_render_ignores_project_markers_above_tmpdir`
- `test_check_reports_stale_files`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
