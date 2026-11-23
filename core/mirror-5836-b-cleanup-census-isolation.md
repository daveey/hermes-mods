# Isolated cleanup process-census fixtures

Source patch ordinals: 5543–5546.

## Behavior

Cleanup tests inspect their own real processes and sandbox directory holders without requesting sensitive attributes from unrelated host processes. Production cleanup logic is unchanged.

## How it works

The autouse fixture wraps `psutil.process_iter` only for `hermes_constants_scratch` and `hermes_cli.kanban_workspace_jobs`. It retains the current process, recursive descendants even outside sandbox roots, and processes whose cwd is within canonical fixture roots. Linux classification reads the procfs cwd link before any requested attributes, avoiding an extra stat of unrelated deleted directories. Explicit synthetic iterators still override the default.

## Code

- `tests/_fixtures/process_census.py`
- `tests/conftest.py`
- `tests/test_cleanup_process_isolation.py`

## Tests

Source test coverage only; **NOT executed this mirror pass**. No test result or deployment verification is claimed.

`tests/test_cleanup_process_isolation.py` covers foreign deleted cwd exclusion, scratch pruning and owned-job scans, real descendants and reparented cwd holders, current-process membership, sibling path boundaries, disappearing processes, recursive census, canonical sandbox roots and synthetic overrides.

## Applying

Apply as test infrastructure with the existing sandbox fixtures. Use the repository test runner so isolation is installed; these tests reject unsupported discovery. Replay the complete ordered mirror using the snapshot's README/policy recipe; this document is not a standalone patch-application guarantee.

## Gotchas

This is not a general process-enumeration restriction. Unrelated integration callers retain normal discovery. Fixtures never signal or modify actual bystanders.

## plugin?

No. This is pytest isolation infrastructure.
