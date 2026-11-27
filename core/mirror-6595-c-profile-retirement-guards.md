# Retired profile home guards

**Scope:** Late writers cannot resurrect renamed/deleted profiles.

**Source ordinals:** 6446, 6447, 6448, 6450

## Behavior

- Update-check, release-verdict and schema-column caches and home initialisation no longer recreate a profile retired (renamed/deleted) mid-operation: raw recursive parent creation is replaced by the existing guarded helper. Missing or tombstoned named homes refuse cache publication without losing the parsed result.

## How it works

- `hermes_cli/source_check.py`, `config_home.py`, `hermes_state_schema.py`.

## Code

Files touched by the series (paths relative to the source tree):

- `hermes_cli/config_home.py`
- `hermes_cli/source_check.py`
- `hermes_state_schema.py`

## Tests

Source test files changed or added by the series:

- `tests/hermes_cli/test_home_init_retirement.py`
- `tests/hermes_cli/test_source_check.py`
- `tests/hermes_state/test_schema_cache_profile_liveness.py`

These were read as part of the mirror from the source diffs only. **Source test coverage was NOT executed in this mirror run**; no pass/fail result is claimed.

## Applying

Treat this series (source ordinals 6446-6450, 4 commits: 6446, 6447, 6448, 6450) as one dependency unit: later commits fix, harden or test earlier ones, so apply them together and in ordinal order rather than cherry-picking. Series-level ordering and the overall mirror procedure are described in [README.md](README.md). Applying means mirroring source into a checkout; it does not install, activate or deploy anything.

## Gotchas

- Live homes keep caching.
- Nothing in this document is a deployment or live-behaviour claim.

## Why core, not a plugin?

Home creation is core.
