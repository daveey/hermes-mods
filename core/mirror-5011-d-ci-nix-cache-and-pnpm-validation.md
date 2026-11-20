# CI: Nix cache saves without purge and pnpm hash validation

Patches: **4717, 4735, 4736**

- 4717 (`28ad10b8b7`): fix(ci): save Nix cache without purge privileges and fail stalled builds [daveey]
- 4735 (`69de22f307`): [daveey] Warm pnpm Nix hash validation without trusting cached output
- 4736 (`5a616b9f7d`): [daveey] Use portable Bash shebang in pnpm validator fixture

Mirrored in the 4653–4752 pass (batch d). Source tests were not freshly executed by this mirror pass; the notes below come from the normalized patches.

## Behavior

- Nix cache save runs without purge privileges (purge disabled; store GC retained); stalled Nix builds fail as command failures; Docker exports final layers only.
- pnpm validation restores the flake store read-only, materializes then rebuilds the fixed-output path so a stale declared hash cannot mask a changed lockfile, and bounds both passes to 15 minutes.

## How it works

`.github/workflows/nix.yml`, `docker.yml`, and `scripts/ci/check_pnpm_deps_hash.sh`, checked by workflow-text tests.

## Code

- `.github/workflows/docker.yml`
- `.github/workflows/nix.yml`
- `scripts/ci/check_pnpm_deps_hash.sh`

## Tests

Landed with the source commits; not rerun here. Names are tests the patches added (a file with no names was modified only).

- `tests/ci/test_nix_cache_budget.py`: `test_cache_save_does_not_purge_with_read_only_token`, `test_nix_timeout_is_a_command_failure`, `test_docker_exports_final_layers_only`
- `tests/ci/test_nix_pnpm_cache.py`: `test_pnpm_restores_without_saving_and_bounds_validation`, `test_validation_rebuilds_cached_output_and_reports_mismatch`

## Applying

Do not apply these patches independently. Apply them as part of the full segmented recipe in the mods/core README **Applying** section, in series order; they depend on earlier core files and are not certified standalone. To re-implement on a newer upstream, port the Code files above and keep the listed tests green.

## Gotchas

- Workflow behavior is only proven on live CI; local tests check workflow text.
- Validator scripts use a portable bash shebang.
