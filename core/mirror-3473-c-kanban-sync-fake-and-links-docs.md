# Kanban sync: fake release validation and links-timing docs

Patches: **3472–3473**.

## Behavior
- 3472 (test-support only): the fake Board service now models the real release policy checks. A bad `failure_limit` (must be a positive safe integer after JS-style coercion; explicit null is refused) or `block_kind` (must be transient, assignment or capability; default transient) is `invalid`, after the fence check. A missing or null `run_uid` is `invalid` before the fence. A `spawn_failed` release with a bad policy is now `invalid` rather than raising `NotImplementedError`.
- 3473 (no behavior change): documents that the "chat links unchanged, skipped" timing counts connection borrow, lock wait, board mode and the inputs read, and that `_links_unchanged` is not a pure predicate (it pops and, on skip, restores `_LINKS_DONE[key]`). The client imports `ledger_slices` / `links_signature` under their public names.

## How it works
3472 extends `FakeService.release_inputs`, `release_op` and the fake hub's leases route in `kanban_sync_fake_board.py`. 3473 edits comments/docstrings and import names in `kanban_sync_client.py`.

## Code
- `tests/hermes_cli/kanban_sync_fake_board.py`
- `tests/hermes_cli/test_kanban_sync_rejections.py`
- `hermes_cli/kanban_sync_client.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_sync_rejections.py`: `test_fake_release_spawn_failed_is_fenced_first`, `test_fake_release_rejects_bad_failure_policy`, `test_fake_release_accepts_valid_failure_policy`, `test_fake_release_without_run_uid_is_invalid`.
- 3473 adds no tests (documentation and import renaming; untested in these patches).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- 3472 changes only a test fake; production release behavior is not altered.
