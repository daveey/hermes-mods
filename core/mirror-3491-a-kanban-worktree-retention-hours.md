# Kanban worktree GC: sub-day retention in hours

Patches: **3477**.

## Behavior
- New optional config key `kanban.worktree_retention_hours` (non-negative number). When set, retention is hours / 24 days and takes precedence over `kanban.worktree_retention_days`; unset or null behaves exactly as before (integer days).
- `0` disables age-based GC. An invalid value (negative, bool, string, NaN/inf, list/map) disables age-based GC with the same warning as an invalid days key and never falls back to days.
- An explicit `kanban gc --worktree-retention-days N` still wins.
- Only the window changes; terminal-owner recheck, fail-closed custody, live-job protection and archive-before-remove are untouched.
- Reverse by removing the hours key.

## How it works
3477 makes `retention_days()` in the worktree GC module read the hours key first and return a float; callers only use the value as `days * 86400` and `days <= 0`, so their annotations widen from int to float. The config defaults file documents the new key.

## Code
- `hermes_cli/config_defaults.py`
- `hermes_cli/kanban_worktree_gc.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_gc_retention_hours.py`: `test_hours_precedence`, `test_unset_hours_keeps_days_type_exactly`, `test_invalid_hours_disables_and_warns`, `test_hours_cutoff_archives_old_tree_and_keeps_young_one`, `test_hours_cutoff_reports_pending_deadline`, `test_unset_hours_keeps_day_cutoff`, `test_invalid_hours_keeps_everything`, `test_explicit_cli_days_still_wins`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- An invalid hours value disables age-based GC entirely rather than using the days key, so a typo stops cleanup.
