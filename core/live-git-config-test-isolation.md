# Live Git configuration test isolation

Patch: 0657 (c431bbb32e).

## Behavior

Test subprocesses do not inherit caller Git-directory, worktree, index, or object-location overrides. The live Git config guard snapshots `core.bare` along with user identity, restores changed values, and reports the offending test. An inherited `GIT_DIR` must not make a scratch `git init --bare` reinitialize a real repository.

## How it works

The per-test environment removes Git location variables. The session guard records protected configuration values and restores both additions and modifications.

## Code

`tests/conftest.py`, `tests/test_live_git_config_guard.py`, `tests/test_live_git_config_guard_core_bare.py`.

## Tests

`scripts/run_tests.sh tests/test_live_git_config_guard.py tests/test_live_git_config_guard_core_bare.py`: 17 passed, one skip in the mirror run. The four core.bare regression cases are included.

## Applying

Apply after 0656. Included to preserve the public append order before 0658.

## Gotchas

This is a test-harness guard, not an operating-system sandbox, and does not prove the identity of a historical config writer. No plugin is involved.
