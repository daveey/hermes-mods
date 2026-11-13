# Test and pm environments borrow the account's verified tool store

Patches: **3210, 3211, 3230**.

## Behavior
- `scripts/run_tests.sh` with a custom (or faked) `HERMES_HOME` borrows the account's tool store; the store's own root never borrows, and a root with its own store keeps it.
- A borrower declines an optional default that the account store never installed; an optional default at another version is still not shared (3211).
- In `pm`, a fresh `HERMES_HOME` on the same filesystem as the account home links each tool entry whose pin (version, target, artifact hashes) matches, after verifying the linked tree hashes to the recorded digest; otherwise it downloads. It shares the account's uv cache, skips eager bytecode compilation for its own environments, and links unchanged source-snapshot files from the donor generation. Linking failure falls back to copying; cross-target stages never link; Windows copies.

## How it works
- 3210 and 3211 extend `scripts/tool_store_pins.py` and the shell helper in `run_tests.sh`.
- 3230 adds `account_home_root()` in `pm/environments.py` and seeding logic in `pm/install.py`, `pm/environment.py`, `pm/packages.py`, `pm/workspace.py`.

## Code
- `scripts/run_tests.sh`
- `scripts/tool_store_pins.py`
- `pm/environments.py`
- `pm/install.py`
- `pm/environment.py`
- `pm/packages.py`
- `pm/workspace.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/scripts/test_tool_store_pins.py`: `test_a_custom_hermes_home_borrows_the_account_store`, `test_a_faked_home_borrows_the_account_store`, `test_the_stores_own_root_never_borrows_it`, `test_a_root_with_its_own_store_keeps_it`, `test_an_optional_default_the_store_never_installed_is_declined_by_the_borrower`, `test_an_optional_default_at_another_version_is_still_not_shared`.
- `tests/pm/test_account_seed.py`: `test_borrowing_home_links_the_account_entry_without_downloading`, `test_link_failure_copies_the_verified_entry`, `test_seed_that_vanishes_mid_link_falls_back_to_download`, `test_tampered_seed_is_never_linked`, `test_seed_on_another_pin_is_not_used`, `test_unreadable_or_unvouched_account_facts_download`, `test_account_home_installs_never_seed_from_themselves`, `test_failed_seed_leaves_nothing_in_the_downloaded_tree`, `test_cross_target_stages_are_never_linked`, `test_account_home_root_is_only_another_home_on_the_same_filesystem`, `test_no_account_home_means_no_borrowing`, `test_borrowing_home_shares_the_account_uv_cache`, `test_borrowing_home_environments_compile_bytecode_lazily`, `test_sync_passes_compile_bytecode_only_when_wanted`, `test_snapshot_links_identical_files_from_the_donor`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Borrowed tools are hard/symbolic links into the account store; modules compile on first import in borrowing homes.
