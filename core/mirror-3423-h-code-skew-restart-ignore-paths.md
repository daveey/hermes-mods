# Code skew ignores tests, docs and CI-only commits

Patches: **3400**.

## Behavior
- A checkout move whose commits touch only tests, docs, CI config, or top-level README/CONTRIBUTING/SECURITY files (and the mailmap) is not code skew: no "Restart required", no skew retirement and no update-pause skew release fires for it. This avoids restarting the gateway and every serve backend on commits that cannot change runtime behavior.
- Markdown in general is not ignored: skill, agent-instruction, persona and prompt files are runtime inputs.
- Ranges are restart-free only when a strict ancestor range has every commit (merges checked against each parent) touching only ignored paths; empty, equal, reversed or bogus ranges, empty commits only, revert pairs over code, and merges bringing code are not restart-free. A git failure fails closed.

## How it works
- 3400 adds `hermes_cli/restart_ignore_paths.py` holding the single exact glob list (`RESTART_IGNORE_GLOBS`) and `range_restart_free()`; `detect_code_skew()` in `gateway/code_skew.py` treats such drift as no skew. The sync script can read the same tuple from HEAD to fast-forward such ranges without a restart (outside this patch).

## Code
- `hermes_cli/restart_ignore_paths.py`
- `gateway/code_skew.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_restart_ignore_paths.py`: `test_ignored_paths`, `test_runtime_paths`, `test_globs_stay_a_plain_literal_tuple`, `test_tests_only_range_is_restart_free`, `test_mixed_range_is_not`, `test_prompt_and_skill_markdown_is_not`, `test_a_revert_pair_over_code_is_not`, `test_empty_equal_reversed_and_bogus_ranges_are_not`, `test_empty_commits_only_are_not`, `test_merge_bringing_code_is_not`.
- `tests/hermes_cli/test_web_server_skew_exit.py`: `test_detect_code_skew_ignores_restart_free_drift`, `test_detect_code_skew_fails_closed_when_git_fails`, `test_default_probe_keeps_a_backend_whose_checkout_moved_by_tests_only`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The glob list must stay a plain literal tuple so external tooling can read it from HEAD.
