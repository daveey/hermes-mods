# Separate CI polling from test execution

Patches: **0330**, **0331**, **0338**.

## Behavior

- The CI review-comment poller targets `[self-hosted, hermes-hub-poll]`, leaving heavy test-runner slots available.
- The CI `all-checks-pass` and `ci-timings` jobs and supply-chain `aggregate` job use the same poll-only label and `${{ !cancelled() }}` rather than `always()`. Cancelled runs need not wait for a heavy runner just to finish aggregation; non-cancelled failure reporting remains eligible.
- Detect's timeout increases to five minutes for loaded shared runners.
- Actionlint recognizes the poll-only label.

These are workflow configuration changes, not runner provisioning. The labels are retained as executable routing identifiers, not host connection details.

## How it works

`scripts/fork-ci-runner.sh` preserves the poller and aggregate-job routing when reapplying fork CI settings after upstream merges. It rewrites only the named aggregation jobs' cancellation predicates and runner labels, and raises the Detect timeout from its old one-minute value.

## Code

| Patch | Source | Scope |
|---|---|---|
| 0330 | `3ffbbbee9b26ad65b2206f1a95801983a02fc2fc` | Review poller workflow and fork routing script |
| 0331 | `52e8ce804dc8d41e911c7028fc64865ea36ef46d` | Actionlint label registration |
| 0338 | `4a97e9b694d9fadd18379a0dcad900e6d8c030d6` | CI/supply-chain aggregators, Detect timeout, routing script and tests |

## Tests

On Linux, the applied public series passed all **6 tests**, with **0 failures**, using:

```sh
bash scripts/run_tests.sh tests/ci/test_fork_runner_routing.py -j1
```

The suite parses workflow configuration to check that no `always()` job targets a heavy runner, all three named aggregates use poll-only routing and skip cancellation, Detect allows at least five minutes, and actionlint registers the label. Poller routing and script rewrites were inspected; script replay/idempotence and actual GitHub cancellation/scheduling were not exercised by this mirror run. No runner deployment or live capacity claim is made.

The full **280-patch** series applied successfully using `git am -3 --empty=keep` at merge-base `4957bbd0a0f6a1c3fe8217dc4a66d072971f5b63`, through fork `98b35b76a1`. Existing privacy scrubs and patch 0251's merge adaptation were retained. Incident-specific prose was removed from the poller commit message.

## Applying

Apply the full ordered series from its merge-base. This merged branch inserts commits before previously published entries: broker transport is now 0335 and PR park handoff is now 0350. The existing patches were moved without changing their bytes, and their documentation references were updated.

## Gotchas

Provision compatible self-hosted runners with the poll-only label before relying on these workflows. This mirror does not create runner services, alter CPU quotas, or validate their availability. Cancellation predicates are not substitutes for required-check evaluation.

## Plugin relationship

None: these are repository CI workflows and maintenance tooling, not a runtime plugin.
