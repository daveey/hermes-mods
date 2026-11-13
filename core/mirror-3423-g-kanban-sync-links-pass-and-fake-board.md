# Kanban sync links pass serialization and fake-board fidelity

Patches: **3322, 3326, 3346**.

## Behavior
- Only one links reconcile pass runs per process even across loop restarts; a restarted loop's runner refuses while a stopped runner's pass still holds the lock, and logs one warning per hold with how long it has been held.
- The fake Board used in tests now matches the real release rules: non-object `extra` is refused, numbers are counted like `JSON.stringify`, `spawn_failed` on ops is refused.

## How it works
- 3326 adds a process-wide `_PASS_LOCK`, resets `_LINKS_RUNNER`, and adds a run-ref prefilter test.
- 3346 records the hold (runner, start, token) and warns once per hold.
- 3322 changes the test fake only.

## Code
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_sync_links_pass.py`

## Tests
Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_sync_rejections.py`: `test_fake_release_rejects_non_object_extra`, `test_fake_release_null_extra_is_empty`, `test_fake_release_diagnostic_counts_numbers_like_json_stringify`, `test_fake_release_spawn_failed_is_not_modelled`.
- `tests/hermes_cli/test_kanban_sync_divergence_prefilter.py`: `test_prefilter_compares_run_refs_by_uid`.
- `tests/hermes_cli/test_kanban_sync_links_thread.py`: `test_a_restarted_loop_never_runs_a_pass_beside_the_stopped_loops`, `test_a_pass_refused_behind_a_stopped_loops_pass_warns_once`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- A hung reconcile still blocks later passes; the patch only makes it visible.
