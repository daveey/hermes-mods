# test(gateway): control reply metrics clock to avoid scheduler flakes

Patch 0932, source `8e836d76ea98360901dd588ed10665a78b4f3db0`.

## Behavior

test(gateway): control reply metrics clock to avoid scheduler flakes. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def rows(monkeypatch):`
- `def test_one_logical_delivery_is_one_row_whatever_the_retries(rows):`

## Code

- `tests/gateway/test_platform_shared_metrics.py`

## Tests

Source test files changed by this commit:

- `tests/gateway/test_platform_shared_metrics.py`

New or changed test definitions present in the source diff:

- `test_first_reply_latency_is_one_row_per_turn_stopped_only_by_reply_text`
- `test_a_relay_delivered_turn_stops_the_clock_its_inbound_started`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
