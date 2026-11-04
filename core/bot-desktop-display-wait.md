# bot_desktop start() waits for the published DISPLAY

Patches: **1918** (`b9eaa8e842`, `bot_desktop: start() waits for the published DISPLAY, not just the env file`).

Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

## Behavior

- `bot_desktop` `start()` returns only once the env file names a DISPLAY, not when the file merely exists, so `status()` no longer reports not-running beside a live launcher.

## How it works

`tools/bot_desktop/runtime.py` `_spawn_and_wait` polls for the DISPLAY value.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1918: _spawn_and_wait returned once the env file existed; status() reports running only when that file names a DISPLAY. A launcher writing it in place (open/truncate, then write — every test fake's `printf > env`) exposes an existing, empty file for a moment, and a throttled runner caught start() in it: running=False, display=None beside a live launcher (flake in test_orphaned_x_server_of_a_dead_launcher_is_reaped_on_next_start).

## Code

`tools/bot_desktop/runtime.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/tools/test_bot_desktop_runtime.py`: `test_start_waits_for_the_published_display_not_just_the_env_file`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
