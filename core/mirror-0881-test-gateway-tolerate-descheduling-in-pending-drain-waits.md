# test(gateway): tolerate descheduling in pending-drain waits [daveey]

Patch 0881, source `1e2946561105817caf7d53155dde2bc2f10255ca`.

## Behavior

test(gateway): tolerate descheduling in pending-drain waits [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `from gateway.platforms.event import MessageEvent, MessageType`
- `async def test_pending_drain_keeps_active_session_guard_live():`
- `async def test_finally_cleanup_drains_late_arrival_pending():`
- `async def test_no_pending_cleans_up_normally():`

## Code

- `tests/gateway/test_pending_drain_race.py`

## Tests

Source test files changed by this commit:

- `tests/gateway/test_pending_drain_race.py`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
