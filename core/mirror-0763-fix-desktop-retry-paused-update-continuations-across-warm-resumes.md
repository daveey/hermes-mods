# fix(desktop): retry paused update continuations across warm resumes [daveey]

Patch 0763, source `0864c0ca49baa980e38d927d84b4aa18432567c6`.

## Behavior

fix(desktop): retry paused update continuations across warm resumes [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `+"""Paused updates recover without a user send, including a warm Desktop reattach."""`
- `def _resume_reuse_live(ctx: _Resume, sid: str, session: dict) -> dict:`
- `from .method_ctx import bind_module`
- `def _maybe_schedule_auto_continue(sid: str, session: dict, session_key: str) ->`
- `def pending_resumes(home: Path, cfg: dict | None, *, now: float | None = None) -`
- `def resume_interrupted_sessions(resume: Callable[[str, dict], Any], home: Path,`
- `def start_startup_resume_sweep() -> threading.Thread | None:`

## Code

- `tui_gateway/methods_session.py`
- `tui_gateway/session_auto_continue.py`
- `tui_gateway/startup_resume.py`

## Tests

Source test files changed by this commit:

- `tests/tui_gateway/test_update_pause_recovery.py`

New or changed test definitions present in the source diff:

- `test_connected_desktop_reattach_retries_after_admission_hold`
- `test_old_connected_backend_waits_for_new_head`
- `test_live_record_not_ready_for_recovery_is_left_alone`
- `test_startup_sweep_retries_warm_record_after_hold`
- `test_boot_worker_retries_without_another_client_message`
- `test_in_place_watcher_retries_until_hold_lifts`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
