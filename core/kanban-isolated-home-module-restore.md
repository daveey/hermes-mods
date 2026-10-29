# Isolated kanban-home fixtures restore the process module graph

Patch: 0732 (fork `b3c050ed13`). Test harness only.

## Behavior

`isolated_kanban_home` (and the same wipe in `isolated_kanban_home_with_profiles`) drops `hermes_cli`, `hermes_state` and `hermes_constants` from `sys.modules` so the next import sees the fixture's `HERMES_HOME`. On the way out it puts the process's module objects back. Before, the wipe stuck: a later file in the same pytest process patched the object it imported at collection, and the code path ran the other copy. That is why an ordered shard reported dozens of failures once an earlier hang no longer stopped the process.

## How it works

`reimported_hermes_modules()` saves the matching `sys.modules` entries, deletes them, yields, then deletes whatever the body imported and writes the saved objects back.

## Code

- `tests/hermes_cli/test_kanban_cli_dispatch_passthrough.py`: `reimported_hermes_modules`, `isolated_kanban_home`
- `tests/hermes_cli/test_kanban_per_profile_cap.py`: the profiles fixture uses the same save/restore

## Tests

`test_reimported_hermes_modules_restores_process_objects` in `tests/hermes_cli/test_kanban_cli_dispatch_passthrough.py`: the module object after the context manager is the one imported before it, and the object imported inside is a different copy. Ran on fork `origin/main` `a65917ae05` for this mirror: passed (1 case, in the 31-passed run recorded in [cron-finalize-slow-write.md](cron-finalize-slow-write.md)).

No production entry path. The per-profile fixture is the same restore, not a second behavior.

## Applying

Append after 0731. Test-only; nothing to configure.

## Gotchas

A new fixture that deletes `hermes_*` from `sys.modules` and does not restore them will split the process the same way. Use `reimported_hermes_modules` (or the same finally) rather than a bare delete.

## Plugin?

No.
