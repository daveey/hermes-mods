# fix(tests): keep Codex picker usage workers out of image recorder [daveey]

Patch 0829, source `aef9f9a55da8b608ac7937d4c90e4e61e8fedac8`.

## Behavior

fix(tests): keep Codex picker usage workers out of image recorder [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def test_full_picker_discovers_codex_models_with_pinned_canonical_url(monkeypatc`

## Code

- `tests/hermes_cli/test_codex_credential_host_binding.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_codex_credential_host_binding.py`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
