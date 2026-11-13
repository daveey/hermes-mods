# Kanban test harness: portable fd paths and JS-faithful sync fake

Patches: **3619–3620**.

## Behavior
- The scratch-archive fsync tests resolve file-descriptor paths on macOS as well as Linux, so the fsync-order contract is covered on both.
- The kanban sync fake's release path coerces inputs like the Board's `releaseTask`: ECMAScript `Number()`/`String()` semantics and `??` defaults for `block_kind`, `outcome`, `error` and the failure limit.

## How it works
- 3619 changes `_fd_path` to use `fcntl` `F_GETPATH` on darwin and `/proc` on Linux (both realpath-resolved) and compares expected paths in resolved form.
- 3620 adds `_js_to_number` (ASCII digits, JS whitespace set, unsigned 0x/0o/0b, arrays via String) and `_js_string`; both fake release routes read outcome as `String(outcome ?? "released")` and error as `String(error ?? outcome)`; `release_op` uses `max_retries ?? Number(failure_limit)` with default 2.

## Code
- `tests/hermes_cli/test_kanban_scratch_gc_archive.py`
- `tests/hermes_cli/kanban_sync_fake_board.py`
- `tests/hermes_cli/test_kanban_sync_rejections.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_sync_rejections.py`: `test_fake_js_to_number_matches_js_number`, `test_fake_release_failure_limit_uses_js_number`, `test_fake_js_string_matches_js_string`, `test_fake_release_block_kind_coerced_like_js_string`, `test_fake_release_outcome_without_nullish_default_is_invalid`, `test_fake_release_outcome_and_error_use_nullish_default`, `test_fake_release_op_failure_limit_nullish`.
- `tests/hermes_cli/test_kanban_scratch_gc_archive.py`: existing fsync tests modified; the patch adds no new test names.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Test-only changes; no runtime behavior changes.
