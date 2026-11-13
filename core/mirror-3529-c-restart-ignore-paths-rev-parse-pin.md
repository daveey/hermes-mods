# restart_ignore_paths: rev-parse no-answer pin

Patches: **3525**.

## Behavior
- When `git rev-parse` times out or errors after `merge-base` exited 128, the verdict is "unknown" and is not cached; once rev-parse answers, the pair gets the definite cached result.
- Comments no longer claim a missing sha "never comes back": a sha can appear later (deepened shallow checkout, ref written before objects). A cached False keeps that pair skewed until the cache clears or the process restarts: fail closed, at worst one extra restart.

## How it works
- 3525 adds a test for the `found is None` branch of `_names_no_commit` and rewords comments in `restart_ignore_paths` and `code_skew`. No logic change.

## Code
- `gateway/code_skew.py`
- `hermes_cli/restart_ignore_paths.py`
- `tests/hermes_cli/test_restart_ignore_paths.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_restart_ignore_paths.py`: `test_a_rev_parse_that_gives_no_answer_after_merge_base_128_stays_uncached`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- A cached "missing commit" verdict persists until cache clear or restart.
