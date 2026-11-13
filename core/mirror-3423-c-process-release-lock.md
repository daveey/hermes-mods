# Process registry release never rewrites without its lock

Patches: **3056, 3057, 3084**.

## Behavior
- Releasing a foreground process no longer rewrites `processes.json` unless the checkpoint file lock was actually entered; an unlocked rewrite could clobber a concurrent writer's entries.
- A recovery racing a real foreground-to-background handoff tracks the command once, with no deadlock and exit status preserved.

## How it works
- 3056 makes `_enter_checkpoint_lock` report whether it took the lock; `release_foreground` skips its read-merge-write otherwise but still pops the hold and settles the entry via `drop_from_every_checkpoint`.
- 3057 and 3084 are documentation-only: `True` means the lock context was entered (its own flock failure is swallowed), and the lock-less drop only tries to remove the entry.

## Code
- `tools/process_registry_survivable.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tools/test_update_survivable_processes.py`: `test_release_never_rewrites_the_checkpoint_without_its_lock`, `test_recovery_racing_a_real_handoff_tracks_the_command_once`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
