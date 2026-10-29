# fix(tests): deflake kanban notifier tests; 10s wait_for deadline timed out on throttled runner (now 120s)

Patch 0864, source `101b35845e2350338ad1663d16c73945cc5762ab`.

## Behavior

fix(tests): deflake kanban notifier tests; 10s wait_for deadline timed out on throttled runner (now 120s). This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `async def test_notifier_notify_plus_wake_sends_and_wakes(kanban_home):`
- `async def test_notifier_plain_notify_never_wakes_even_with_session_id(kanban_hom`
- `async def test_notifier_notify_wake_does_not_wake_on_status_event(kanban_home):`
- `async def test_notifier_wake_forwards_persisted_chat_type_and_user_id(kanban_hom`
- `async def test_notifier_wake_only_skips_send_and_advances_cursor(kanban_home):`
- `async def test_notifier_unsubs_after_abnormal_events(kind, kanban_home):`
- `async def test_notifier_wakes_origin_for_review_and_keeps_subscription(kanban_ho`
- `async def test_notifier_artifact_delivery_skips_missing_files(kanban_home, tmp_p`
- `async def test_notifier_uploads_review_handoff_artifacts(kanban_home, tmp_path,`

## Code

- `tests/hermes_cli/test_kanban_notify.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_notify.py`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
