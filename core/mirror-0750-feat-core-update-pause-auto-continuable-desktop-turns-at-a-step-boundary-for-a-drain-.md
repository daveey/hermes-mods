# feat(core-update): pause auto-continuable Desktop turns at a step boundary for a drain window [daveey]

Patch 0750, source `cc29d783ed4fe3aef41b5b84cc2f4e8224b084ee`.

## Behavior

feat(core-update): pause auto-continuable Desktop turns at a step boundary for a drain window [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _run_conversation_turn(`
- `def run_conversation(`
- `class IterationStart:`
- `def begin_iteration(`
- `def clear_hold(home):`
- `+"""[daveey] A core-update pause ends a turn only at a step boundary.`
- `+"""[daveey] The core-update pause request: hermes-sync's drain window asks auto-continuable Desktop turns to`
- `+"""[daveey] Core-update pause: a Desktop turn the restarted backend can auto-continue pauses at a step`
- `def _record_turn_marker(session: dict, text: Any, *, auto_continue: bool = True,`
- `def _invoke_agent(`
- `def _run_admitted_prompt(`
- `def _retire_turn_marker(session: dict, *keys: str) -> None:`
- `def _maybe_schedule_auto_continue(sid: str, session: dict, session_key: str) ->`
- `def _update(home: Path | str, session_key: str, mutate, what: str) -> None:`
- `def clear_turn_marker(home: Path | str, session_key: str) -> None:`
- `def read_turn_marker(home: Path | str, session_key: str) -> dict[str, Any] | Non`
- `+"""[daveey] Core-update pause for Desktop/TUI turns (t_01a1827558218fed).`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> hermes-sync's drain window held update admission until every turn in flight ended, so one
> long autonomous turn held the update for whole windows, again and again (t_01a1827558218fed).
>
> The drainer now writes <root>/core-update/pause-request {version, expires_at} beside its hold
> once every remaining turn is one the updated backend auto-continues. admission_fence.pause_requested
> honours it only under the hold and before expires_at. The agent loop consults a per-turn
> step_boundary_pause hook after a step's tool results are appended and persisted, before the
> next model call, never mid-tool or mid-call. tui_gateway/update_pause.py installs the hook,
> declines turns the startup sweep would not resume (resume_on_startup off, opted out, attempt or
> pause cap, stale, hosted room), stamps the marker reason=core_update_pause, keeps it past the
> terminal frame, and shows 'Paused for a Hermes update; continues automatically'. The turn's
> admission reservation is released as the turn thread ends, so the update applies. The restarted
> backend's sweep continues the turn with a pause note that uses no crash attempt (pauses are
> counted apart, max 3). If the window ends without an update, a bounded watcher continues the turn in place.

## Code

- `agent/conversation_loop.py`
- `agent/turn_iteration_prep.py`
- `hermes_cli/admission_fence.py`
- `tui_gateway/prompt_turn.py`
- `tui_gateway/session_auto_continue.py`
- `tui_gateway/turn_marker.py`
- `tui_gateway/update_pause.py`

## Tests

Source test files changed by this commit:

- `tests/agent/test_step_boundary_pause.py`
- `tests/hermes_cli/test_admission_fence_pause.py`
- `tests/tui_gateway/test_update_pause.py`

New or changed test definitions present in the source diff:

- `test_pause_requested_during_a_running_tool_ends_the_turn_after_it`
- `test_hook_declining_lets_the_turn_run_to_completion`
- `test_no_hook_is_unchanged`
- `test_not_consulted_before_the_first_model_call`
- `test_no_request_file_means_no_pause`
- `test_request_under_the_hold_is_active`
- `test_request_without_the_hold_is_ignored`
- `test_expired_request_is_ignored`
- `test_unreadable_request_is_ignored`
- `test_clear_removes_the_request`
- `test_no_request_no_pause`
- `test_resumable_turn_pauses_and_its_marker_survives_the_turn_end`
- `test_turn_that_cannot_be_auto_continued_is_untouched`
- `test_paused_turn_releases_its_reservation_keeps_its_marker_and_says_so`
- `test_turn_without_a_request_ends_normally`
- `test_next_user_turn_retires_its_own_marker_again`
- `test_continuation_turn_carries_the_pause_count_not_an_attempt`
- `test_paused_turn_continues_with_the_pause_note_and_no_attempt_used`
- `test_crash_marker_still_uses_the_crash_note`
- `test_startup_sweep_picks_up_a_paused_turn_of_a_dead_backend`
- `test_in_place_resume_decision`
- `test_marker_writer_is_this_process`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
