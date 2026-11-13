# Process recovery never adopts a held foreground command

Patches: **3033, 3045**.

## Behavior
- Update-time process recovery skips foreground commands the terminal tool is still waiting on, so a call that exited 0 is no longer reported lost; release drops the hold and its checkpoint entry together; a still-held command is refused tracking with an error log; a dead owner's held command is still recovered.
- The configuration docs name the commands `terminal.update_survivable_processes` covers: a background job without a PTY, or a foreground command with a timeout above 60 seconds.

## How it works
- 3033 makes recovery skip ids in `_foreground_holds` under the registry lock, makes release and `convert_foreground` act under the checkpoint file lock, and refuses `_track_started` for held commands.
- 3045 is a documentation edit.

## Code
- `tools/process_registry.py`
- `tools/process_registry_checkpoint.py`
- `tools/process_registry_survivable.py`
- `website/docs/user-guide/configuration.md`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tools/test_update_survivable_processes.py`: `test_recovery_never_adopts_a_held_foreground_command_of_this_registry`, `test_recovery_racing_a_foreground_release_never_adopts_it`, `test_release_drops_the_entry_even_while_its_log_and_exit_file_remain`, `test_recovery_racing_a_handoff_tracks_the_command_once`, `test_adoption_boundary_refuses_a_held_foreground_command`, `test_a_dead_owners_held_command_is_still_recovered`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
3045 is documentation only.
