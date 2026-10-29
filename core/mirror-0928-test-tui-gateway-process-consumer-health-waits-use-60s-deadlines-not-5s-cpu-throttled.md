# test(tui_gateway): process-consumer-health waits use 60s deadlines, not 5s (CPU-throttled runners)

Patch 0928, source `9bff6b3d9302b32d690ccc98e3cea91041f0f31c`.

## Behavior

test(tui_gateway): process-consumer-health waits use 60s deadlines, not 5s (CPU-throttled runners). This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def consumer(tmp_path, monkeypatch):`
- `def test_completion_before_spawn_returns_still_reaches_consumer(consumer, tmp_pa`
- `def available(c, home):`
- `def test_refused_turn_never_certifies(consumer, tmp_path, accepted):`
- `def test_stale_receipt_and_notifications_off(consumer, tmp_path, monkeypatch):`
- `def test_native_stop_destroys_receipt_even_after_cancel_latch_clears(consumer, t`
- `def test_late_success_after_stop_cannot_resurrect_receipt(consumer, tmp_path, mo`

## Code

- `tests/tui_gateway/test_process_consumer_health.py`

## Tests

Source test files changed by this commit:

- `tests/tui_gateway/test_process_consumer_health.py`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
