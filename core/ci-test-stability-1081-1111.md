# CI lanes, cache budget and test deflakes, 1081-1111

Patches:

- `patches/1081-test-deflake-skill-mutation-lock-test-generous-waits.patch`
- `patches/1083-fix-tests-deflake-engagement-closed-day-test-join-th.patch`
- `patches/1085-fix-tests-deflake-R6-line-two-freshness-check-bound-.patch`
- `patches/1091-fix-ci-separate-completion-gates-from-live-comment-p.patch`
- `patches/1093-fix-test-avoid-pytest-logger-registry-capture-race-2.patch`
- `patches/1094-ci-setup-pm-stop-the-bootstrap-writing-bytecode-into.patch`
- `patches/1095-ci-lint-bound-optional-PM-tools-saves-outside-automa.patch`
- `patches/1096-test-ci-stall-the-cache-TLS-proxy-without-overriding.patch`
- `patches/1097-test-ci-use-case-insensitive-workflow-proxy-keys-onl.patch`
- `patches/1098-test-ci-keep-unmeasured-cache-stall-smoke-off-the-fa.patch`
- `patches/1101-tests-cron-drain-the-shared-metrics-worker-before-te.patch`

Test-only deflakes plus CI workflow changes from the 1081-1111 range.

## Behavior

- 1081: the skill-mutation lock test uses generous waits and asserts the writers finished.
- 1083: the engagement closed-day test joins the export worker and drains deferred writes before reading.
- 1085: the R6 line-two freshness check bounds age by the 20 min threshold and waits on progress rather than four fixed sleeps.
- 1091: completion gates are separated from live-comment pollers in the workflows and fork-runner routing (`.github/FAST-LANE.md`, `scripts/fork-ci-runner.sh`).
- 1093: a pytest logger-registry capture race is avoided (`pyproject.toml`, `uv.lock`, bootstrap-installer workflow).
- 1094: `setup_toolchain.py prepare` no longer writes bytecode into `pm/`, so the PM tools cache key stays stable (before: unique per job, 0 hits, a ~114 MB save per lint job).
- 1095: optional PM tools cache saves are bounded outside automatic post hooks; a cache-save-timeout smoke workflow is added.
- 1096, 1097, 1098: the smoke workflow stalls the cache TLS proxy without overriding runner credentials, uses case-insensitive proxy keys once, and stays off the fast lane.
- 1101: the cron test conftest drains the shared-metrics worker before teardown (re-cache race).

## How it works

Only test code and CI configuration change; no runtime behavior.

## Code

`tests/tools/test_skill_manager_tool.py`, `tests/hermes_cli/test_shared_metrics_engagement.py`, the desktop update POSIX handoff protocol test, `.github/workflows/*`, `.github/actions/setup-pm/action.yml`, `scripts/fork-ci-runner.sh`, `scripts/ci/setup_toolchain.py`, `tests/ci/*`, `tests/scripts/*`, `tests/cron/conftest.py`.

## Tests

These patches are the tests. 1091 changes `tests/ci/test_fast_runner_routing.py` and `tests/ci/test_fork_runner_routing.py`; 1093 adds `tests/scripts/test_pytest_logging_concurrency.py`; 1094 and 1095 change `tests/scripts/test_setup_toolchain.py`; 1095 adds `tests/ci/test_lint_cache_budget.py`. They were added or changed by these commits; they were not re-run for this mirror.

## Applying

Apply the listed patches in order with `git am -3 --empty=keep`; no config is required unless named above.
