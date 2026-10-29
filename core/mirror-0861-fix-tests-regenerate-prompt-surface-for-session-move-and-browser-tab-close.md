# fix(tests): regenerate prompt surface for session_move and browser tab close

Patch 0861, source `5e206f07aa5ffbcfef3c0c5d99976ab35a1420da`.

## Behavior

fix(tests): regenerate prompt surface for session_move and browser tab close. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `"stable": 1935,`
- `"patch",`
- `"stable": 1445,`
- `"stable": 1866,`
- `"stable": 2640,`
- `"stable": 1317,`
- `"stable": 1579,`
- `"react_to_message",`
- `"stable": 1349,`
- `"stable": 1382,`
- `{`
- `+{`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> 59009a4979 added the session_move tool without re-rendering
> tests/fixtures/prompt_surface, and 410bb33433 added the close_tab
> sentence to browser_exec. test_committed_prompt_surface_is_current
> fails on both. Re-rendered hermetically (TMPDIR outside the home
> tree); the diff is session_move.json, that tool on every surface,
> and the close_tab sentence in browser_exec.json.

## Code

- `tests/fixtures/prompt_surface/surfaces.json`
- `tests/fixtures/prompt_surface/tools/browser_exec.json`
- `tests/fixtures/prompt_surface/tools/session_move.json`

## Tests

Source test files changed by this commit:

- `tests/fixtures/prompt_surface/surfaces.json`
- `tests/fixtures/prompt_surface/tools/browser_exec.json`
- `tests/fixtures/prompt_surface/tools/session_move.json`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
