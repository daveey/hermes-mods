# A same-name backup plugin dir never shadows the live plugin

Patches:

- `patches/0210-fix-plugins-a-same-name-backup-dir-never-shadows-the.patch`: backup-dir skip, same-name resolution, `plugins list` and `doctor` reporting.

## Behavior

- Discovery skips backup-named plugin dirs (`*.bak*`, `*.orig`, `*.old`, case-insensitive) with a WARNING
  ("move it to <home>/plugin-backups/").
- When two dirs in one plugins-dir level declare the same manifest key, the dir whose basename equals the name wins
  (else the first in sorted order) and a WARNING names both paths.
- `hermes plugins list` prints ignored/shadowed copies (yellow, below the header; on stderr in `--plain`/`--json`).
- `hermes doctor` has a new "Plugin Directories" section (warning + manual issue).

Why: a hub profile had `slack-workspace.bak-premacops/` (`name: slack-workspace`) next to `slack-workspace/`. The sorted
scan with later-wins loaded the backup, so three owner-DM fixes never ran live.

## How it works

`scan_directory(conflicts=)` collects `PluginDirConflict` records; `_dedupe_same_name` picks the winner;
`plugin_dir_conflicts` exposes them to the CLI and doctor.

## Code

- `hermes_cli/plugins_discovery.py`: `is_backup_plugin_dir`, `PluginDirConflict`, `_dedupe_same_name`,
  `scan_directory(conflicts=)`, `plugin_dir_conflicts`.
- `hermes_cli/plugins_cmd.py` (`_scan_level`), `hermes_cli/plugins_cmd_listing.py` (`_print_dir_conflicts`).
- `hermes_cli/doctor_state.py` (`_check_plugin_dir_conflicts`), `hermes_cli/doctor.py` (`DOCTOR_CHECKS`).

## Tests

`tests/hermes_cli/test_plugin_dir_conflicts.py` (15): a backup never shadows the live copy (red on the old code), each
suffix is skipped, a basename match wins in either scan order, no match keeps the first and warns, a clean tree is
silent, `plugin_dir_conflicts` records, listing picks the live copy, `plugins list` prints shadowed paths, doctor
warns / stays quiet. Not re-run when this doc was written (untested here).

## Applying

`git am -3` in order.

## Gotchas

- Keep backups out of plugin dirs entirely: move them to `<home>/plugin-backups/`.
- The check is per plugins-dir level; the same name in the default and a profile dir is not a conflict.

## Plugin?

No. Discovery precedence is core; no plugin hook runs before discovery. (A private companion, outside this repo,
evicts such dirs automatically.)
