# CI: reserve the fork's Actions cache for Nix, and judge a warm Nix cache by HTTPS source fetches

Mirror entries 5146, 5186.

## Behavior

- 5146: in the fork only, the Docker workflow disables both reads and writes of the GitHub Actions cache backend. Docker's `mode=min` selects layers but is not a byte budget, immutable blobs from many commits crowd out the Nix entries in the shared repository quota, and reads would keep refreshing old blobs' recency. Upstream keeps its existing policy; builds, publishing, runner selection and Nix's 5 GiB / 10-copy budgets are unchanged.
- 5186: the Nix cache summary judges a warm cache by the number of HTTPS source fetches (budget 10) rather than the count of paths copied from the public binary cache. Advisory warning semantics, main-push and primary-key-hit gates, both counters, the 5 GiB rooted ceiling and the store-size GC limit are kept.

## How it works

- `.github/workflows/docker.yml` carries a fork-only condition on the cache-from/cache-to settings.
- `scripts/ci/nix_cache_summary.py` counts log lines for source fetches versus cached-path copies.

## Code

- `.github/workflows/docker.yml`
- `scripts/ci/nix_cache_summary.py`

## Tests

Source tests (not executed in this mirror pass):

- `tests/ci/test_nix_cache_budget.py`: `test_fork_docker_cache_cannot_compete_with_nix`, `test_docker_exports_final_layers_only`.
- `tests/ci/test_nix_cache_observations.py`: `test_summary_counts_real_log_lines`, plus the 10/11 source-fetch boundary and cold-run-shaped apparent hit.

Whether the cache actually stays populated on a real fork run is untested here.

## Applying

These entries are part of the complete ordered mirror series. Apply them only through the segmented Applying recipe in the core mods [README](README.md): the full ordered patch list with `git am -3 --empty=keep`, the replay bridges, and the mandatory escape repair that README names. They are not independent patches and this document is not a standalone certification: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- Fork-only: the condition must be kept when merging upstream workflow changes.
- Cache quota and eviction are service behavior; the workflow change is a mitigation, not a guarantee.

## Why core instead of a plugin

These are repository CI workflow and script files, not runtime code a plugin could carry; they ship with the tree.
