# CI cache timeout and duration-fixture reliability

Patches: 3793, 3808.

## Behavior

Bounds macOS reuse cache lookup/save steps to two minutes, and makes the parallel test runner's slow-case probe tolerate scheduling delays without weakening its duration floor.

## How it works

The workflow adds `timeout-minutes: 2` to both reuse-cache operations. The reuse-key module comment clarifies that the CI workflow file enters the key only when the PR itself changes it; an unrelated mainline workflow edit intentionally preserves the key.

The slow-case fixture parses all timing lines into case/duration pairs instead of requiring exactly one line beginning with a `0.` duration. It still requires the sleeper at or above its floor, permits only the fixture's two test names and rejects below-threshold reported durations.

## Code

- `.github/workflows/ci.yaml`
- `scripts/ci/macos_state_reuse.py`

## Tests

**Tests were not rerun by this documentation pass.** The assertions below describe the staged test diffs, not fresh execution results. Full replay verification is owned by the parent mirror pass; no deployment or live-runtime claim is made.

- `tests/ci/test_macos_state_reuse.py` (patch 3793).
- `tests/scripts/test_run_tests_parallel_slow_cases.py` (patch 3808).

- **3793:** Workflow structure assertions require both cache lookup and save timeout-minutes values to equal two.
- **3808:** Parsed slow-case lines may name only the fixture cases, must include the sleeper at least 0.6 seconds, and every reported case must meet the 0.4-second reporting floor.

## Applying

Use the complete ordered series and the [README Applying recipe](README.md#applying), including its historical integration bridges and ancestry-only handling where listed. Then run the mandatory snapshot-bound [hostname escape repair](hostname-escape-repair.md) and repaired-tree checks specified there. These ordinals are documentation groupings, **not independent cherry-picks**; do not apply just this subset or a one-glob series that omits bridges. The parent mirror pass must establish replay/tree verification for the final snapshot.

## Gotchas

The workflow timeout bounds the cache step, not every test step. A slow runner can make the nominally fast fixture appear in slow-case output too; this does not invalidate the runner. No production test-runner timing logic changes in the duration patch.

## Plugin?

No. Workflow limits and test harness assertions are repository CI concerns.
