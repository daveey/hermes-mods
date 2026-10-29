# fix(tui_gateway): a notification turn no longer swallows a core-update-paused turn [daveey]

Patch 0783, source `d55299af3eb632e0319f256cace6333c3f0255bc`.

## Behavior

fix(tui_gateway): a notification turn no longer swallows a core-update-paused turn [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `+"""[daveey] A synthesized turn (background-process completion, kanban notice, subagent result) that lands on`
- `def _admit_prompt_turn(`
- `def _record_turn_marker(session: dict, text: Any, *, auto_continue: bool = True,`
- `def _run_admitted_prompt(`
- `def _retire_turn_marker(session: dict, *keys: str) -> None:`
- `def _notif_run_submit(rid, sid, session, text, **kwargs):`
- `def in_place_resume_action(session: dict, marker_key: str, *, skew: Callable[[],`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> A synthesized turn (process completion, kanban notice, wake-up) that landed on a session paused for a
> core update wrote its own crash marker over the core_update_pause one and cleared it on completion, and
> the in-place watcher read the busy session as 'user moved on'. The paused request was never continued
> (David-MBP session 5ab65c, 2026-10-28 07:50). Synthesized turns now run without a marker of their own
> while a paused one exists, the watcher waits for them, and they re-arm the watcher on completion; a typed
> message still retires the pause.

## Code

- `tui_gateway/prompt_turn.py`
- `tui_gateway/session_auto_continue.py`
- `tui_gateway/session_notifications.py`
- `tui_gateway/update_pause.py`

## Tests

Source test files changed by this commit:

- `tests/tui_gateway/test_update_pause_superseded.py`

New or changed test definitions present in the source diff:

- `test_notification_turn_on_a_paused_session_keeps_the_paused_turn_continuable`
- `test_watcher_waits_while_a_synthesized_turn_runs_over_the_pause`
- `test_typed_user_message_on_a_paused_session_still_retires_the_pause`
- `test_notification_turn_without_a_pause_leaves_no_marker`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
