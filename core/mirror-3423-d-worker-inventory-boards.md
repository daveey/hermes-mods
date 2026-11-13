# Worker inventory board registry hardening

Patches: **3114, 3130, 3136, 3150, 3156**.

## Behavior
- Tests never register boards in the real account worker inventory, including children that rebuilt a stripped environment.
- A registered scratch/tmp board whose directory was garbage-collected retires its record instead of making every admission refuse with `worker_resources_unknown`.
- Only "file not found" counts as missing in every inventory existence check; permission errors and a regular file where a directory is expected refuse rather than silently dropping boards from the capacity census.
- A record unlinked between listing and reading is skipped; an unreadable board keeps its registration after a failed removal.

## How it works
- 3114 retires GC'd scratch boards in `paths()` and stops tests registering in the real inventory (a test that undid its monkeypatch had dropped the isolation).
- 3130 guards registration with the shared test-context detector (env markers, isolation marker, pytest ancestry); `paths()` only skips a collected record, while `register()`, under the account admission fence, unlinks it.
- 3136 skips records unlinked mid-listing and replaces `Path.exists()` with `stat()` where only `FileNotFoundError` means absent.
- 3150 adds the single shared `worker_board_retirement.missing()` helper (stat plus `FileNotFoundError`), because `Path.exists()` swallows all OSErrors on Python 3.14 but raises on 3.11–3.13.
- 3156 documents that ENOTDIR propagates by design and pins the removal and regular-file-parent cases.

## Code
- `hermes_cli/worker_account_boards.py`
- `hermes_cli/worker_resource_admission.py`
- `hermes_cli/worker_board_retirement.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_worker_account_boards.py`: `test_garbage_collected_scratch_board_retires_its_record`, `test_missing_board_stays_unknown_unless_its_scratch_dir_is_gone`, `test_ephemeral_roots_never_cover_the_account`, `test_pytest_never_registers_in_the_real_account_inventory`, `test_concurrent_register_of_recreated_scratch_board_keeps_its_record`, `test_stripped_env_child_with_isolation_marker_is_refused`, `test_record_unlinked_between_listing_and_read_is_skipped`, `test_unreadable_collected_looking_board_is_not_retired`, `test_unreadable_inventory_location_refuses_instead_of_dropping`, `test_unreadable_registration_record_is_not_rewritten`, `test_retirement_missing_and_discard_treat_only_enoent_as_absent`, `test_regular_file_inventory_parent_refuses_instead_of_dropping`, `test_regular_file_board_slug_is_no_board`, `test_unreadable_board_after_failed_removal_keeps_registration`.
- `tests/hermes_cli/test_worker_resource_admission.py`: `test_unknown_names_the_failing_check_once`.
- `tests/hermes_cli/test_worker_board_retirement.py`: no new test names read from the patch.
- `tests/hermes_cli/test_kanban_worker_pid_fingerprint.py`: changed in 3114; no new test names read.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Behavior of `Path.exists()` is Python-version dependent, which is why a single helper is used.
