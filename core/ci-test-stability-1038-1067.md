# CI lanes, cache budget and test deflakes, 1038-1067

Patches:

- `patches/1038-test-deflake-skill-mutation-lock-test-generous-waits.patch`
- `patches/1040-fix-tests-deflake-engagement-closed-day-test-join-th.patch`
- `patches/1042-fix-tests-deflake-R6-line-two-freshness-check-bound-.patch`
- `patches/1048-fix-ci-separate-completion-gates-from-live-comment-p.patch`
- `patches/1050-fix-test-avoid-pytest-logger-registry-capture-race-2.patch`
- `patches/1051-ci-setup-pm-stop-the-bootstrap-writing-bytecode-into.patch`
- `patches/1052-ci-lint-bound-optional-PM-tools-saves-outside-automa.patch`
- `patches/1053-test-ci-stall-the-cache-TLS-proxy-without-overriding.patch`
- `patches/1054-test-ci-use-case-insensitive-workflow-proxy-keys-onl.patch`
- `patches/1055-test-ci-keep-unmeasured-cache-stall-smoke-off-the-fa.patch`
- `patches/1057-tests-cron-drain-the-shared-metrics-worker-before-te.patch`

Test-only deflakes plus CI workflow changes from the 1038-1067 range.

## Behavior

- 1038: the skill-mutation lock test uses generous waits and asserts the writers finished.
- 1040: the engagement closed-day test joins the export worker and drains deferred writes before reading.
- 1042: the R6 line-two freshness check bounds age by the 20 min threshold and waits on progress rather than four fixed sleeps.
- 1048: completion gates are separated from live-comment pollers in the workflows and fork-runner routing (`.github/FAST-LANE.md`, `scripts/fork-ci-runner.sh`).
- 1050: a pytest logger-registry capture race is avoided (`pyproject.toml`, `uv.lock`, bootstrap-installer workflow).
- 1051: `setup_toolchain.py prepare` no longer writes bytecode into `pm/`, so the PM tools cache key stays stable (before: unique per job, 0 hits, a ~114 MB save per lint job).
- 1052: optional PM tools cache saves are bounded outside automatic post hooks; a cache-save-timeout smoke workflow is added.
- 1053, 1054, 1055: the smoke workflow stalls the cache TLS proxy without overriding runner credentials, uses case-insensitive proxy keys once, and stays off the fast lane.
- 1057: the cron test conftest drains the shared-metrics worker before teardown (re-cache race).

## How it works

Only test code and CI configuration change; no runtime behavior.

## Code

`tests/tools/test_skill_manager_tool.py`, `tests/hermes_cli/test_shared_metrics_engagement.py`, the desktop update POSIX handoff protocol test, `.github/workflows/*`, `.github/actions/setup-pm/action.yml`, `scripts/fork-ci-runner.sh`, `scripts/ci/setup_toolchain.py`, `tests/ci/*`, `tests/scripts/*`, `tests/cron/conftest.py`.

## Tests

These patches are the tests. 1048 changes `tests/ci/test_fast_runner_routing.py` and `tests/ci/test_fork_runner_routing.py`; 1050 adds `tests/scripts/test_pytest_logging_concurrency.py`; 1051 and 1052 change `tests/scripts/test_setup_toolchain.py`; 1052 adds `tests/ci/test_lint_cache_budget.py`. They were added or changed by these commits; they were not re-run for this mirror.

## Applying

Apply the listed patches in order with `git am -3 --empty=keep`; no config is required unless named above.
