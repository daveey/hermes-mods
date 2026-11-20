# Lifecycle guard: scan scripts the same command writes before running

Patches: **4718, 4719, 4720**

- 4718 (`b9cec91e75`): [daveey] lifecycle guard: scan a script the same command writes before it runs it
- 4719 (`0bd2abac8b`): [daveey] lifecycle guard: a bare or exec redirection and a cp/mv of a generated script fail closed
- 4720 (`fb146e85f2`): [daveey] lifecycle guard: fail closed on parameter-expanded script writers

Mirrored in the 4653–4752 pass (batch d). Source tests were not freshly executed by this mirror pass; the notes below come from the normalized patches.

## Behavior

- Redirection and `tee` writes are recorded with known content (quoted heredoc, here-string, literal echo/printf) and scanned wherever the path is run.
- Unknown writers (other commands, bare or exec redirections, cp/mv/ln/install destinations, parameter-expanded content) fail closed on execution only.

## How it works

New `cron/lifecycle_guard_generated.py` wired through `cron/lifecycle_guard.py`.

## Code

- `cron/lifecycle_guard.py`
- `cron/lifecycle_guard_generated.py`

## Tests

Landed with the source commits; not rerun here. Names are tests the patches added (a file with no names was modified only).

- `tests/cron/test_lifecycle_guard_generated_scripts.py`: `test_generated_script_is_scanned_as_written`, `test_stale_file_on_disk_does_not_hide_the_written_one`, `test_printed_and_herestring_writes_are_scanned`, `test_unpredictable_writer_then_execute_fails_closed`, `test_generated_lifecycle_command_is_refused`, `test_inert_or_benign_generated_files_are_allowed`, `test_parameter_expanded_writer_fails_closed`

## Applying

Do not apply these patches independently. Apply them as part of the full segmented recipe in the mods/core README **Applying** section, in series order; they depend on earlier core files and are not certified standalone. To re-implement on a newer upstream, port the Code files above and keep the listed tests green.

## Gotchas

- Variable paths remain a known gap.
