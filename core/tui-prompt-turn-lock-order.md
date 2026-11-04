# TUI gateway: prompt turns start without history_lock under _sessions_lock

Patches: **1824** (`cb4dab32c2`, `[daveey] fix(tui_gateway): start a prompt turn without taking history_lock under _sessions_lock`), **1825** (`db46a8d3df`, `[daveey] fix(tui_gateway): a cancelled prepared turn worker settles when its sibling deferred to it`), **1826** (`4ef1b79ab6`, `[daveey] fix(tui_gateway): give a prepared turn admission back when the start path raises`), **1827** (`4c2aa2a801`, `[daveey] test(tui_gateway): intercept the prompt-turn spawn at its new prepare seam`), **1828** (`e075ca231e`, `test(tui): local turn harness for the notice-commit lock-order test (no imported fixture shadowing)`).

Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

## Behavior

- `prompt.submit` prepares its turn admission (which takes `history_lock`) before taking `_sessions_lock`, so a `session.resume` racing the same session's submit no longer deadlocks the backend (1824).
- A cancelled prepared worker settles the turn when its sibling deferred settlement to it (1825).
- If the start path raises under `_sessions_lock` (for example a routing-row reopen), the prepared admission is cancelled or aborted after the lock is released instead of leaking (1826).
- Tests intercept the spawn at the new prepare seam (1827) and use a local turn harness for the notice-commit lock-order test (1828).

## How it works

`tui_gateway/turn_admission.py` splits admission into prepare/start; `tui_gateway/prompt_turn.py` prepares outside `_sessions_lock` and starts under it. The source branch then merged main's notice-commit context manager (`start_synthesized_turn`) in `1bf6faea509`; that merge's hand resolution is carried by the replay bridge before 1828 (see the verification report).

Source commit notes (first paragraph of each message, privacy-normalized):

- 1824: C14 (multiclient session model e2e) wedged the backend: a session.resume racing its session's own prompt.submit deadlocked, and every later create/resume queued behind the stuck _sessions_lock (seed 11 resume hung 60 s, seeds 23/37 could not create a session).
- 1825: A sibling worker of the same turn that finishes while prepared work is counted but unstarted skips settlement; cancel() now leaves as the last worker would, so the turn settles as it did before the work was prepared. Test is red without it.
- 1826: A raise under _sessions_lock before the spawn (routing-row reopen) leaked the prepared admission; clean-up now runs after the lock is released (cancel before spawn, abort for a failed spawn, nothing once the worker started). Also drops the blind catch flagged by code health.
- 1827: [daveey] test(tui_gateway): intercept the prompt-turn spawn at its new prepare seam
- 1828: test(tui): local turn harness for the notice-commit lock-order test (no imported fixture shadowing)

## Code

`tui_gateway/prompt_turn.py`, `tui_gateway/session_lifecycle.py`, `tui_gateway/turn_admission.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/tui_gateway/test_bot_live_owner_delivery.py`
- `tests/tui_gateway/test_notice_commit_lock_order.py`: `test_notice_turn_start_marks_delivery_without_history_lock_under_sessions_lock`
- `tests/tui_gateway/test_prompt_start_lock_order.py`: `test_registered_turn_start_takes_history_lock_outside_sessions_lock`, `test_resume_rebind_racing_turn_start_does_not_deadlock`, `test_turn_refused_for_a_closing_session_gives_back_the_prepared_admission`, `test_cancelled_work_settles_a_turn_whose_sibling_worker_finished_meanwhile`, `test_turn_start_failure_releases_the_prepared_admission_outside_sessions_lock`
- `tests/tui_gateway/test_session_start_requests.py`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
