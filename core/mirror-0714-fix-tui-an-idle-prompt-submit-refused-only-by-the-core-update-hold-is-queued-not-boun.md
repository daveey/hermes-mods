# fix(tui): an idle prompt.submit refused only by the core-update hold is queued, not bounced with 5035 [daveey]

Patch 0714, source `b54002e7047105f739031fa20e75d1e8c4ebb16e`.

## Behavior

fix(tui): an idle prompt.submit refused only by the core-update hold is queued, not bounced with 5035 [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `+"""An idle prompt.submit refused only by the core-update admission hold is queued, never bounced.`
- `def _(rid, params: dict) -> dict:`
- `def _handle_busy_submit(rid, sid: str, session: dict, text: Any, transport: Any,`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> The hermes-sync drainer now holds update admission for a bounded drain window
> while in-flight turns finish (t_01a181e61e88e5d4). Desktop has no 5035 handling,
> so a send to an idle chat during that window surfaced as an error. The message
> now joins the durable queue (persisted at accept, like a mid-turn send) and the
> existing admission retry runs it when the hold lifts. A retiring backend and a
> rewind keep their 5035.

## Code

- `tui_gateway/methods_prompt.py`
- `tui_gateway/session_auto_continue.py`

## Tests

Source test files changed by this commit:

- `tests/tui_gateway/test_update_hold_submit_queue.py`

New or changed test definitions present in the source diff:

- `test_idle_submit_under_the_update_hold_is_queued_and_runs_after_it`
- `test_second_send_under_the_hold_merges_into_the_same_queue`
- `test_retiring_backend_keeps_its_5035`
- `test_a_rewind_under_the_hold_is_refused_not_queued`
- `test_without_a_hold_the_submit_is_admitted_normally`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
