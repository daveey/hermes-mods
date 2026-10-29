# fix(tests): arm process-consumer notifications before spawn [daveey]

Patch 0888, source `ea821b5e1871f7f3ea2a0018ef181fea63b2458e`.

## Behavior

fix(tests): arm process-consumer notifications before spawn [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `from tests.waiting import wait_until`
- `def consumer(tmp_path, monkeypatch):`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> The reader can finish before spawn_local returns; enabling notify_on_complete afterwards loses the one-shot completion. Configure fixture sessions before their reader starts and force completion-before-return for successful and refused admission.

## Code

- `tests/tui_gateway/test_process_consumer_health.py`

## Tests

Source test files changed by this commit:

- `tests/tui_gateway/test_process_consumer_health.py`

New or changed test definitions present in the source diff:

- `test_completion_before_spawn_returns_still_reaches_consumer`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
