# e2e-upgrade runs on bubblewrap-capable hub slots

Patch **0684** (`74c5d8f71f`).

## Behavior

The `e2e-upgrade` job in `.github/workflows/tests.yml` requests the `ci-bwrap` self-hosted label in addition to `self-hosted` and `hermes-hub`. No other job in that workflow requests `ci-bwrap`. The suite's own bubblewrap probe is unchanged.

Plain hub slots cannot run bubblewrap (Docker's default seccomp, AppArmor, masked `/proc`, and the host's unprivileged-userns restriction). Slots labeled `ci-bwrap` are the ones built so that probe can pass.

## How it works

`runs-on` for `e2e-upgrade` is `[self-hosted, hermes-hub, ci-bwrap]`. `.github/actionlint.yaml` lists `ci-bwrap` as a known self-hosted label so actionlint accepts it.

## Code

`.github/workflows/tests.yml`, `.github/actionlint.yaml`, `tests/ci/test_fast_runner_routing.py`.

## Tests

`tests/ci/test_fast_runner_routing.py::test_e2e_upgrade_runs_only_on_bwrap_capable_slots` asserts:

- the `e2e-upgrade` job labels are `self-hosted`, `hermes-hub`, `ci-bwrap`
- `ci-bwrap` is declared in the actionlint self-hosted label list
- no other job in the scanned workflows requests `ci-bwrap`

This mirror did not re-run that test. A live `e2e-upgrade` shard on a `ci-bwrap` slot is untested here. The probe itself is not modified and is not newly tested.

## Applying

`git am -3` 0684. Workflow and test only.

## Gotchas

- Adding the label without a runner that advertises it leaves the job queued. The label is an ops contract, not something this patch creates.
- Do not point other jobs at `ci-bwrap`. The routing test fails if another job takes the label.

## plugin?

No. GitHub Actions runner selection.
