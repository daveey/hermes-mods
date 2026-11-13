# Test deflakes: admission trace, free-tier turn, scope inode

Patches: **3083, 3098, 3099**.

## Behavior
- Test-only fixes; no runtime behavior changes.
- The API admission test swaps its writer trace only under the database lock to avoid a deadlock.
- The free-tier prompt-turn test waits for real worker threads instead of patching `threading.Thread`.
- The legacy-scope stale-record test derives the stale inode from the recreated directory.

## How it works
- 3083 changes `test_api_admission_off_loop` locking; 3098 uses `tests.waiting.wait_until` for message completion; 3099 sets the stale inode to the recreated directory's inode + 1.

## Code
- `tests/gateway/test_api_admission_off_loop.py`
- `tests/tui_gateway/test_free_tier_task_done.py`
- `tests/hermes_cli/test_worker_legacy_scopes.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- Existing tests modified, no new test names: `tests/gateway/test_api_admission_off_loop.py`, `tests/tui_gateway/test_free_tier_task_done.py` (`test_prompt_submit_records_only_untouched_turns`), `tests/hermes_cli/test_worker_legacy_scopes.py` (`test_stale_records_are_ignored`).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
