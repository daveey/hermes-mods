# Credential singleton read and home-path fast paths

Mirror batch 6; ordinals 4028, 4029.

## Behavior

Credential singleton dispatch avoids loading auth storage in branches that do not consume it. Home-path optimization caches immutable Path syntax only, leaving environment/context selection live.

## How it works

Auth reads move into the consuming provider branches, retaining source-suppression checks. An LRU bounded to 128 expanded strings reuses Path construction after live variable/tilde expansion. Warm home-key hits look up the raw filesystem string before allocating a Path. Test modules reject unsupported unittest discovery.

## Code

Source paths touched by this feature group (including regression fixtures):

- `agent/credential_pool.py`
- `hermes_constants.py`
- `tests/agent/test_pool_singleton_lazy_read.py`
- `tests/test_home_key_fast_hit.py`
- `tests/test_home_path_construction.py`

Mail entries in the ordered series:

- 4028: [`12948c50a7d143794b1afd963710ba88f53a5052`](patches/4028-perf-picker-remove-redundant-singleton-reads-and-home-path-allocation-daveey.patch)
- 4029: [`de6a264f0b1abb370528e1b254ec20458e842af3`](patches/4029-test-picker-guard-new-home-tests-against-unittest-discovery-daveey.patch)

## Tests

- `tests/agent/test_pool_singleton_lazy_read.py`: non-consuming seeders fail if dispatch reads auth; consuming seeders receive the current generation on consecutive calls.
- `tests/test_home_key_fast_hit.py`: warm keys for Path and string inputs succeed even when Path construction is replaced by a failure.
- `tests/test_home_path_construction.py`: identical expanded syntax reuses identity while environment, tilde, process/nested overrides and relative cwd resolution remain current. Patch 4029 adds canonical-runner discovery guards to both home test modules.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results. Source reports, where quoted, are historical/reported evidence only; replay verification belongs to the parent mirror task.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks. Use the README’s complete series recipe, not a glob of just this feature’s patches.

## Gotchas

No new credential/config/provider cache is introduced. Historical source performance numbers are not measurements executed by this documentation pass.

This document makes no deployment or live-display verification claim; those checks are owned separately.

## Plugin?

No. These are core lifecycle/state contracts or their regression tests; an external plugin cannot replace the underlying ownership boundary.
