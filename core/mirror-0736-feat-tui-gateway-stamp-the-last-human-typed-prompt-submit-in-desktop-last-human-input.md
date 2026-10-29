# feat(tui_gateway): stamp the last human-typed prompt.submit in desktop/last_human_input.json [daveey]

Patch 0736, source `5b3fbc10d57085164ab118034b0cd9a391d37b5d`.

## Behavior

feat(tui_gateway): stamp the last human-typed prompt.submit in desktop/last_human_input.json [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `+"""Typed prompts stamp <HERMES_HOME>/desktop/last_human_input.json; injected turns never do. [daveey]`
- `+"""When did a human last type a prompt into a Desktop chat on this host? [daveey]`
- `def _(rid, params: dict) -> dict:`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> hermes-sync opens core-update drain windows (new messages wait up to 15 min) only while the user is
> away; HID idle time misses typing from another Mac's Desktop over desktop-ssh. Injected turns (kanban
> notices, auto-continue, cross-session sends, relay/hosted, hidden intents) never stamp it.
> t_01a1825a82e472f6

## Code

- `tui_gateway/human_input.py`
- `tui_gateway/methods_prompt.py`

## Tests

Source test files changed by this commit:

- `tests/tui_gateway/test_human_input_record.py`

New or changed test definitions present in the source diff:

- `test_typed_prompt_submit_records_the_time`
- `test_queued_composer_drain_still_counts`
- `test_cross_session_send_does_not_record`
- `test_hidden_widget_intent_and_in_process_submits_do_not_record`
- `test_classifier_rejects_relay_and_hosted_submits`
- `test_injected_turn_does_not_record`
- `test_kanban_wake_does_not_record`
- `test_auto_continue_schedule_does_not_record`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
