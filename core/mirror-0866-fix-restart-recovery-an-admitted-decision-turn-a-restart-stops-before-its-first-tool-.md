# fix(restart recovery): an admitted decision turn a restart stops before its first tool is continued at boot; sweep logs every skipped marker; restart failed-turn copy [daveey]

Patch 0866, source `0ee4afb4bf1d90bec5434c684eebabb66208ae40`.

## Behavior

fix(restart recovery): an admitted decision turn a restart stops before its first tool is continued at boot; sweep logs every skipped marker; restart failed-turn copy [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _redact_display_metadata(metadata: dict) -> dict:`
- `def _close_durable_failed_turn(agent, result: Any) -> None:`
- `PARTIAL_FAILED_TURN_NOTICE = (`
- `def untyped_failed_turn_display_kind(role: Any, content: Any) -> Optional[str]:`
- `+"""A turn a backend restart stopped before any tool ran must not tell the user to "Send it again".`
- `+"""An admitted decision whose turn a backend restart stops before any tool ran is continued at the next boot.`
- `def test_crash_after_commit_does_not_reexecute(owner, monkeypatch):`
- `def test_turn_adopts_admission_row_without_duplicate_and_disables_recovery(owner`
- `def _dispatch(server, rid, sid, session, text, profile):`
- `def _record_turn_marker(session: dict, text: Any, *, auto_continue: bool = True,`
- `def _maybe_schedule_auto_continue(sid: str, session: dict, session_key: str) ->`
- `def _homes(home: Path) -> list[tuple[str | None, Path]]:`
- `def pending_resumes(home: Path, cfg: dict | None, *, now: float | None = None) -`
- `def resume_interrupted_sessions(resume: Callable[[str, dict], Any], home: Path,`
- `def _on_tool_start(sid: str, tool_call_id: str, name: str, args: dict):`
- `def _update(home: Path | str, session_key: str, mutate, what: str) -> bool:`
- `def record_turn_start(home: Path | str, session_key: str, prompt: str, *, attemp`
- `def clear_turn_marker(home: Path | str, session_key: str) -> None:`
- `def read_turn_marker(home: Path | str, session_key: str) -> dict[str, Any] | Non`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Self-heal desktop-3a6213ce74: prompt admission dispatched with auto_continue=False, so a hub update that
> restarted the backend 15 s into an approved For-you decision left a marker the boot sweep skipped silently,
> and the chat said 'Send it again' with no button left. The admitted turn's marker now stays
> auto-continuable until its first tool starts (mark_turn_tool_started flips it off before the tool runs,
> keeping at-most-once execution); startup_resume logs each skipped marker with its reason; a failed turn
> stopped by backend_exit before any tool says it continues automatically.

## Code

- `agent/conversation_loop.py`
- `agent/turn_failure_copy.py`
- `tui_gateway/methods_prompt_admission.py`
- `tui_gateway/prompt_turn.py`
- `tui_gateway/session_auto_continue.py`
- `tui_gateway/startup_resume.py`
- `tui_gateway/tool_progress.py`
- `tui_gateway/turn_marker.py`

## Tests

Source test files changed by this commit:

- `tests/agent/test_failed_decision_turn_copy.py`
- `tests/tui_gateway/test_admission_restart_resume.py`
- `tests/tui_gateway/test_prompt_admission.py`

New or changed test definitions present in the source diff:

- `test_failed_decision_turn_copy`
- `test_restart_after_a_tool_ran_keeps_the_partial_copy`
- `test_other_failures_keep_send_it_again`
- `test_close_durable_failed_turn_detects_backend_exit`
- `test_backend_exit_marker_survives_and_boot_sweep_continues`
- `test_admitted_turn_is_not_continued_once_a_tool_started`
- `test_boot_sweep_logs_every_skipped_marker`
- `test_turn_adopts_admission_row_without_duplicate_and_limits_recovery_to_no_tool_turns`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
