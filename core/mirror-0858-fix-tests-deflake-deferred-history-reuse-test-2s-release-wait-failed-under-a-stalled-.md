# fix(tests): deflake deferred-history reuse test; 2s release wait failed under a stalled setter thread [daveey]

Patch 0858, source `2596142bf4a1011773abefb9e87770d4fa31a657`.

## Behavior

fix(tests): deflake deferred-history reuse test; 2s release wait failed under a stalled setter thread [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def test_session_resume_deferred_history_acknowledges_and_reuses(monkeypatch):`

## Code

- `tests/tui_gateway/test_tui_gateway_server.py`

## Tests

Source test files changed by this commit:

- `tests/tui_gateway/test_tui_gateway_server.py`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
