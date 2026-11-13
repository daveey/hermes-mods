# Package manager account-seed opt-out and per-lock seed marker

Patches: **3397**.

## Behavior
- `HERMES_PM_ACCOUNT_SEED=0` turns off borrowing from a live account's tool store/uv cache (also honored by the test runner's tool-store borrowing); other values keep borrowing. The setup-pm toolchain script exports the opt-out to later CI steps so a runner whose login has a live Hermes home on the same filesystem does not restore/save/prune that account's cache.
- A seeded install is hashed once; homes on different locks do not rerun each other's seed (per-lock marker); a repaired seed records the published digest.
- Borrower lifecycle and snapshot rewrites never rewrite an account inode or reach a donor file.
- A macOS state CI lane that does not use setup-pm keeps borrowing; its workflow comment now states what is shared and why it is safe.

## How it works
- 3397 adds `account_seed_enabled` to `pm/environments.py`, marker and hashing changes in `pm/store.py`/`pm/install.py`/`pm/packages.py`, the opt-out export in the setup toolchain script, and documentation in `pm/AGENTS.md`.

## Code
- `pm/environments.py`
- `pm/install.py`
- `pm/packages.py`
- `pm/store.py`
- `pm/AGENTS.md`
- `.github/workflows/hermes-state-macos.yml`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- Test names added by this patch (file paths not individually read): `test_opt_out_borrows_nothing`, `test_other_opt_out_values_keep_borrowing`, `test_homes_on_different_locks_do_not_rerun_each_others_seed`, `test_borrower_lifecycle_never_rewrites_an_account_inode`, `test_snapshot_rewrites_never_reach_a_donor_file`, `test_seeded_install_hashes_the_tree_once`, `test_repaired_seed_records_the_published_digest`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The patch documents a case of a primary home outside the default home beside a stale default home; see `pm/AGENTS.md`.
