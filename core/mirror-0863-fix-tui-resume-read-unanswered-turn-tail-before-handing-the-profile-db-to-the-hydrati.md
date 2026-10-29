# fix(tui resume): read unanswered-turn tail before handing the profile db to the hydration worker; deflakes deferred-resume db ownership test

Patch 0863, source `ae5cb9a99c0af3f7f6dfa17e78f4bea476caf516`.

## Behavior

fix(tui resume): read unanswered-turn tail before handing the profile db to the hydration worker; deflakes deferred-resume db ownership test. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def test_deferred_resume_preserves_model_history_and_db_ownership(tmp_path, monk`
- `def _resume_deferred(ctx: _Resume) -> dict:`

## Code

- `tui_gateway/methods_session.py`

## Tests

Source test files changed by this commit:

- `tests/tui_gateway/test_deferred_model_history.py`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
