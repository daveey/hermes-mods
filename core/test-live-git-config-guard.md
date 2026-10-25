# Protect live git identity during tests

Patches: 0253 0255.

## Behavior

An autouse test guard detects and undoes writes to user.* identity settings in live checkout git config. Its subprocess access is isolated from tests that mock Popen.

## How it works

The fixture snapshots live git configuration and restores identity mutations before failing the offending test. Patch 0255 preserves real subprocess primitives so subprocess mocks do not corrupt guard behavior.

## Code

tests/conftest.py; tests/test_live_git_config_guard.py

## Tests

tests/test_live_git_config_guard.py covers identity changes/restoration and Popen-mock isolation.

Test files above are shipped regression coverage, not a claim that every suite was rerun. See [mirror-verification.md](mirror-verification.md) for this export’s execution results.

## Applying

Apply the ordered core series through the last listed patch using `git am -3`, starting at the fork/upstream merge-base; these are not independent patches.

## Gotchas

This protects the test process, not arbitrary user shell commands. Temporary repositories remain usable by tests.

## Plugin?

This changes core-owned behavior or its test harness; no disk plugin is required.
