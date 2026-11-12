# Retain shell initialization policy on config read failures

Mirror entries: [2324](patches/2324-daveey-Preserve-resolved-shell-init-on-config-read-failures.patch), [2325](patches/2325-daveey-Scope-retained-shell-init-policy-to-the-config-path.patch).

## Behavior

- A transient config read failure retains the last successfully resolved shell-init files and auto_source_bashrc setting for that exact config path.
- If no policy was resolved for that path, fall back to default home profiles rather than borrowing another profile's settings.
- Warn once per exception type/message, and refresh the cached policy on successful reads without changing normal file normalization.

## How it works

tools/environments/local.py caches immutable normalized policy tuples in a process-local dictionary keyed by get_config_path, then returns a fresh file list. Failure looks up only the current path; an unresolved path has no cached policy. The first patch added retention and warning detail; the follow-up replaces the single cache slot with per-path isolation. The source startup regression exercises a configured shell function through actual local shell startup.

## Code

- `tools/environments/local.py`

## Tests

Source test references below are inspection evidence only. **Not rerun by this mirror documentation pass.** No live/runtime or CI-green claim is made.

- `tests/tools/test_local_shell_init_config_failure.py` — `test_read_error_keeps_previous_shell_init`, `test_first_read_error_warns_about_default_home_profiles`, `test_warning_once_per_exception_type_and_message`, `test_success_refreshes_cache_without_changing_normalization` (additional cases in this file).

## Applying

Apply these entries only as part of the ordered mirror series using [README Applying](README.md), including its replay bridges and required post-replay overlays. They are not independent cherry-picks. This documentation pass did not replay or install the series.

## Gotchas

This is process-local last-good retention, not a persistent config backup. It does not validate an unreadable configuration or promise custom init on the first failed read. Warning dedupe is by exception class/message; retained policy is by config path. No live shell or user config was modified in this documentation pass.

## Plugin

No. This changes core behavior or its regression infrastructure; a plugin cannot enforce the same internal invariants.
