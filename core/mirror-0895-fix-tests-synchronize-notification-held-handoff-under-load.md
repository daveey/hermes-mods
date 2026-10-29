# fix(tests): synchronize notification held handoff under load [daveey]

Patch 0895, source `2e952ee5103f9687e0af38bd8a00f6ae08cf5e3f`.

## Behavior

fix(tests): synchronize notification held handoff under load [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `from types import SimpleNamespace`
- `def test_shutdown_drain_returns_held_events_to_the_shared_queue(env):`

## Code

- `tests/tui_gateway/test_notification_held_handoff.py`

## Tests

Source test files changed by this commit:

- `tests/tui_gateway/test_notification_held_handoff.py`

New or changed test definitions present in the source diff:

- `test_busy_owner_among_many_pollers_does_not_bounce_its_event`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
