# Vendored patchelf repair and bounded stable Nix cache roots

Mirror entries: 5125, 5126.

## Behavior

- The patched patchelf derivation uses a vendored source patch rather than fetching that patch during each build.
- CI roots stable outputs before cache post-job garbage collection, measures the closure, and refuses a root larger than the existing five-GiB budget.
- Cache summaries count substituter copies and HTTPS fetches, warn on a main primary-key hit with more than ten copied paths, and remain available on failed builds.

## How it works

`patchelf-fixed.nix` shares the repaired tool derivation with PM packages. The CI cache root includes tools actually built by flake checks, patchelf, the Python environment and pnpm dependencies; the review follow-up removes unnecessary npm/just roots.

`root_nix_cache.sh` realizes and roots the cache output. Budget/summary Python helpers inspect measured closure/log input. Workflow shell uses `tee` with `pipefail`, runs summaries unconditionally and preserves failed build/over-budget status instead of masking it with a successful logging command or continue-on-error.

## Code

Paths below refer to the mirrored core source tree, not standalone plugin files.

- `.github/workflows/nix.yml`
- `nix/packages.nix`
- `nix/patchelf-fixed.nix`
- `nix/patches/patchelf-fix-executable-header-load-overlap.patch`
- `nix/pm-packages.nix`
- `scripts/ci/nix_cache_budget.py`
- `scripts/ci/nix_cache_summary.py`
- `scripts/ci/root_nix_cache.sh`

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

Cache-observation tests cover real log-line counting, primary-key warnings, missing input, budget enforcement after realization, and the actual workflow root shell under build failure, oversized closure and an in-budget control. The review tests pin the root set and unconditional failure propagation. No Nix builds, sandbox checks, cache uploads or two-run warm-cache acceptance were executed by this mirror task; those paths remain untested here.

Source test/coverage paths:

- `tests/ci/test_nix_cache_observations.py`

## Applying

Apply these entries only as part of the **whole ordered 5406 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches, execute source tests or activate runtime behavior.

## Gotchas

Vendoring one repair removes that network fetch, not all upstream dependencies. Stable roots must fit the existing cache quota and be created before garbage collection. Green unit fixtures do not prove warm-cache reuse or Nix sandbox behavior.

## Why core instead of a plugin

These changes belong to the core flake/package derivations and CI cache lifecycle. An installed runtime plugin cannot retain build-time outputs or enforce workflow exit status.
