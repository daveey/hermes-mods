# A client-less auto-continue runs as the session's stored source

Patch: [`patches/0713-fix-tui-a-client-less-auto-continue-runs-as-the-sess.patch`](patches/0713-fix-tui-a-client-less-auto-continue-runs-as-the-sess.patch)
(fork commit `355e6a6a87`, card t_01a183149a00c941)

## Why

A paused or crash-interrupted Desktop turn is continued by the backend with no client attached:

- the startup sweep (`tui_gateway/startup_resume.py`, see [auto-continue-resume.md](auto-continue-resume.md))
  calls `session.resume` in-process;
- the core-update in-place watcher (`tui_gateway/update_pause.py`, see
  [update-pause-in-place-handoff.md](update-pause-in-place-handoff.md)) continues the live record.

The sweep's `session.resume` named no `source`, so `_Resume.mint` fell back to `_resolve_session_platform()`:
`desktop` only when the process has `HERMES_DESKTOP=1` and no `HERMES_DESKTOP_TERMINAL`, else `tui`. On a
backend without that env, a desktop chat continued as `platform=tui`. The agent got TUI interface guidance
("now being answered on a different interface: tui", no MEDIA rendering, no inline previews, no cron
`deliver=origin`) and the desktop-only tools (`drive_preview`) were not loaded. The record kept `source=tui`,
so the turns the user sent after opening the chat ran as TUI too.

On David-MBP on 2026-10-28 at 08:02:39 PDT, serve 40523's sweep continued 20261027_134353_5ab65c (stored
`source=desktop`) with `conversation turn: … platform=tui`.

## Behavior

- **Startup sweep:** `resume_interrupted_sessions` passes `source_from_store: true`. When the resume names no
  explicit `source`, `_Resume.mint` takes the stored `sessions.source` if it is `desktop` or `tui`.
- **Every auto-continue kickoff** (`_maybe_schedule_auto_continue`, shared by the cold resume, warm reuse and
  the in-place watcher) calls `_adopt_stored_continuation_source` before the agent build and again after it:
  - reads the stored source from the agent's own `_session_db`, else the profile's state.db;
  - sets `session["source"]` to it when it differs, and logs `auto-continue for <key> runs as stored source …
    (record had …)`;
  - rebuilds an agent already built for another platform (`_rebuild_session_agent(platform_override=…)`), as
    the in-place watcher continues the agent that ran the paused turn.
- Missing or unknown stored sources (`telegram`, `bot_room`, empty) keep today's default. A failed read or
  rebuild is logged and the continuation runs as before; it never blocks the turn.
- Never inferred from the absent client: an explicit `source` param on a client's resume still wins at mint,
  but the continuation itself always runs as the stored desktop/tui source.

## Code

- `tui_gateway/session_auto_continue.py`: `_STORED_CONTINUATION_SOURCES`, `_stored_continuation_source`,
  `_read_stored_continuation_source`, `_adopt_stored_continuation_source`, two calls in `kickoff()`.
- `tui_gateway/methods_session.py`: `_Resume.mint` honors `source_from_store`.
- `tui_gateway/startup_resume.py`: the sweep sets `source_from_store`.

## Tests

`tests/tui_gateway/test_update_pause_recovery.py` (the backend env forced to resolve `tui`):

- `test_startup_resume_of_a_desktop_session_continues_as_desktop`: continuation build gets
  `platform_override=desktop` and the record keeps `source=desktop`. RED on the old code.
- `test_startup_resume_of_a_tui_session_stays_tui`: env says desktop, stored says tui, runs tui. RED on the old code.
- `test_startup_resume_of_an_unknown_source_keeps_the_default`: stored `telegram` keeps the env default.
- `test_in_place_continuation_of_a_desktop_session_runs_as_desktop`: a record mounted without a source with a
  tui-built agent; the watcher rebuilds it as desktop before the continuation. RED on the old code.
- `test_in_place_continuation_of_a_tui_session_stays_tui`: no rebuild.

`tests/tui_gateway/test_startup_resume.py::test_profiles_are_swept_with_profile_param` pins the new param.

## Verify live

After an update pause or restart continues a desktop chat, `agent.log` shows `conversation turn: session=<id> …
platform=desktop` for the continued turn (and the `runs as stored source desktop` line when the record had
another source).

## Gotchas

- Only `desktop` and `tui` are adopted. Widen `_STORED_CONTINUATION_SOURCES` deliberately: other sources have
  their own recovery paths (hosted rooms are excluded earlier).
- `_rebuild_session_agent` does not close the replaced agent; that matches its other callers (bot capability
  sync, `/new`).
