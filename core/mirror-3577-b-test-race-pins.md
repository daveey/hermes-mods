# Test pins for scratch-archive fixture races and managed-worker teardown

Patches: **3568, 3570**.

## Behavior
- No runtime behavior change; both patches are tests only.
- The scratch-archive test fixture's git calls no longer spawn background maintenance, removing a flaky "scan uncertain" refusal. The archive scan itself still fails closed.
- A managed-worker test pins that when a worker is lost and its `close()` then raises `BrokenPipeError`, `ManagedExecutionUnknown` still propagates and the admission row stays `unknown`.

## How it works
3568 makes the test module's `_run` git helper pass `-c maintenance.auto=false -c gc.auto=0`, and adds a guard that runs seed, clone and commit under `GIT_TRACE2_EVENT` and asserts no `git maintenance` / `git gc` child process starts. 3570 drives a real child that says hello then sends a bogus frame while `ManagedWorker.close` raises, asserting the exception type and admission state.

## Code
- `tests/hermes_cli/test_kanban_scratch_gc_archive.py`
- `tests/gateway/test_managed_worker_drain.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_scratch_gc_archive.py`: `test_fixture_git_never_spawns_background_maintenance`.
- `tests/gateway/test_managed_worker_drain.py`: `test_lost_worker_stays_unknown_when_worker_teardown_raises`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- 3570 pins a fix that landed in an earlier commit not part of this patch; it applies only on a tree that has that fix.
