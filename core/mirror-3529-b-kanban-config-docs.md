# Kanban config docs: budget checkpoint default and worktree retention hours

Patches: **3511, 3518**.

## Behavior
- Documentation states that the Kanban 90% budget checkpoint default takes precedence over the one-shot 80% default, even for one-shot Kanban workers.
- `kanban.worktree_retention_hours` is documented: a non-negative number of hours that overrides `worktree_retention_days` when set (`0` disables age-based GC); unset/null keeps the days behavior; an invalid value disables age-based GC with a warning and never falls back to days.
- The key is seeded as `None` in the default config, so setting it through the config command no longer prints an unknown-key notice; GC semantics are unchanged.

## How it works
3511 edits one sentence in the configuration user guide. 3518 adds the Worktree GC paragraph to the Kanban feature guide, updates the `kanban_scratch_archive` module docstring, and seeds `"worktree_retention_hours": None` in `DEFAULT_CONFIG`, collapsing two config lines into inline comments.

## Code
- `website/docs/user-guide/configuration.md`
- `website/docs/user-guide/features/kanban.md`
- `hermes_cli/config_defaults.py`
- `hermes_cli/kanban_scratch_archive.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_gc_retention_hours.py`: `test_seeded_default_is_unset_and_keeps_days_behaviour`.
- 3511: untested (documentation only).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
