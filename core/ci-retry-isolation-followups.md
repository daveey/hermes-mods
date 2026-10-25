# CI retry, isolation, and sidebar follow-ups

## Behavior

- 0260 (`df1f0f4d`): includes `kanban_active` in the AutomatedRow type; the regression keeps waiting automated rows hidden unless human attention is needed. Desktop rendering is not verified here.
- 0262 (`9b7e8f26`): retries failing pytest node IDs rather than every test in a file; collection errors/crashes retain file fallback. Passing retries remain FLAKY; a second failure stays red. The CI helper deduplicates deflake cards and links recurrences after closure.
- 0263 (`90bd2dc9`): sizes test workers from configured slice CPU/shard budgets, capped by runner quota, with the existing fallback when budgets are absent.
- 0264 (`b60f11ae`): replaces short load-sensitive waits in redirect and hosted-room stop tests with bounded event synchronization; lease expiry is not the stop-retry behavior under test.
- 0265 (`da239bd1`): transports flaky IDs in GitHub warning annotations instead of artifact uploads; the helper reads completed-run annotations.
- 0266 (`120f04d4`): extracts runner reporting helpers for code-health budgets, uses UTF-8 BOM-aware reads, and carries the AutomatedRow type correction. This patch overlaps 0260 and applies through three-way merging.
- 0272 (`4ccd362a`): runs the live Git-config guard after monkeypatch teardown and pins UTF-8 decoding for worker-scope systemctl output.
- 0273 (`88a905ed`): aligns the LSP completion regression with preservation of Git-bearing scratch workspaces and their language servers.

## How it works

The runner parses pytest failure summaries, persists flaky reports, and delegates CI annotation/card handling to dedicated helpers. Worker budgeting is independent of retry policy. Test isolation wraps teardown ordering so mocked environment restoration cannot bypass the live-config check. The LSP test now checks the existing preservation contract rather than deleting a Git-bearing tree.

## Code

`scripts/run_tests_parallel.py`, `scripts/run_tests.sh`, `scripts/ci/{flaky_kanban,test_workers}.py`, `.github/workflows/tests.yml`, `tests/conftest.py`, `hermes_cli/kanban_worker_scope.py`, and `apps/desktop/src/store/sidebar-automated.ts`.

## Tests

On a standalone clone of the complete applied 275-patch series, the canonical `bash scripts/run_tests.sh -j 1` runner executed:

- `tests/ci/test_flaky_kanban.py`: 6 passed (synthetic CI/card inputs, not live GitHub writes).
- `tests/ci/test_test_workers.py`: 7 passed.
- `tests/scripts/test_run_tests_parallel.py`: 32 passed.
- `tests/scripts/test_run_tests_shell.py`: 2 passed.
- `tests/test_live_git_config_guard.py`: 13 passed.
- `tests/agent/lsp/test_workspace_release.py`: 7 passed, 2 failed. Both failures are CLI-worktree cases whose lazy bootstrap reads the real-home parent `manifest.json`, refused by the test home-I/O guard. The Git-bearing Kanban scratch cases pass. The guard was not disabled.

Total: 67 passed, 2 failed. Platform-only cases were skipped by the Linux runner. This is not a fully green suite. The sidebar regression, full redirect/hosted-room suites, non-Linux behavior, live CI annotation ingestion, and actual Desktop rendering were not rerun in this mirror batch. The supplied worker-scope test filename did not exist and was not discovered; no worker-scope runtime test is claimed.

## Applying

Apply the full ordered series from merge-base `4957bbd0a0f6a1c3fe8217dc4a66d072971f5b63`, not these patches independently. The 275-patch `git am -3` verification passed against the export of fork `db355bde416089cc5d955d4b94ab1536de2b1da1` with upstream `1f91da2a27`. Existing privacy substitutions and the 0242 merge adaptation were retained.

## Gotchas

Merge topology inserted eight entries before previously published patches. Existing numbers shifted: 0260→0261, 0261→0267, 0262→0268, 0263→0269, 0264→0270, 0265→0271, 0266→0274, 0267→0275. Consult feature docs by name, not old patch numbers. Earlier verification records describe historical series sizes. No unmerged continuation-guard or Chrome ownership changes are included.

## Plugin?

These changes affect core types, the test runner, and test isolation; they are not a runtime plugin.
