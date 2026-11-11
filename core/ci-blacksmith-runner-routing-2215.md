# CI Linux jobs on Blacksmith runners

Patches:

- `patches/2180-ci-move-Linux-jobs-from-self-hosted-tfgar-lanes-to-B.patch`
- `patches/2181-ci-blacksmith-8vcpu-never-scheduled-on-example-org.patch`
- `patches/2182-ci-test-slices-install-bubblewrap-and-lift-the-usern.patch`
- `patches/2183-ci-only-lift-the-AppArmor-userns-knob-where-the-kern.patch`

The fork's CI ran on one ephemeral self-hosted VM per job (about 10k launches in one day, median queue wait 53 s for a 19 s job) because the personal account's hosted jobs were refused for billing. After the repository moved to an organization with hosted billing, the Linux lanes move to Blacksmith runners, and the follow-ups fix what that exposed.

## Behavior

- Self-hosted lane labels (the fast-check lane, the hub poll/CI lanes, the docker lane and the retired test/bwrap lanes) are replaced by `blacksmith-2vcpu/4vcpu-ubuntu-2404`; the macOS state lane and the probe lane are untouched.
- 2181: 8 vCPU jobs were never scheduled in the new org, so every heavy lane uses 4 vCPU; the Docker release-phase gate (reads `needs` only) runs on 2 vCPU; the stalled-cache-save smoke runs on GitHub-hosted `ubuntu-24.04` because Blacksmith serves `actions/cache` from its own service, so the smoke's blackhole endpoint is never reached.
- 2182/2183: test slices install `bubblewrap` and run a `bwrap` self-test (fails closed); the AppArmor user-namespace sysctl is lifted only when `/proc/sys/kernel/apparmor_restrict_unprivileged_userns` exists, because Blacksmith kernels lack the knob. `plugin-catalog-ci.yml` gets the same guard.
- Repository-identity checks in fork-only gates name the fork repository in its new organization so they keep their meaning after the transfer.

## How it works

`scripts/fork-ci-runner.sh` re-points upstream and retired self-hosted `runs-on:` labels to the Blacksmith sizes and is idempotent (regex rewrite; Docker-socket files and matrix `runner:` values are handled separately and the arm64 leg is dropped). The workflow files carry the result of that script. The routing tests assert the lane of each job.

## Code

About 35 workflow files under `.github/workflows/` (including `tests.yml`, `docker.yml`, `nix.yml`, `repo-guards.yml`, `plugin-catalog-ci.yml`, `cache-save-timeout-smoke.yml`), `.github/actionlint.yaml`, `scripts/fork-ci-runner.sh`, `scripts/ci/{flaky_kanban,nightly_e2e,recover_macos_runner}.py`.

## Tests

Tests are the coverage supplied with the patches. They were **not executed in this mirror**.

`tests/ci/test_fast_runner_routing.py`, `tests/ci/test_fork_runner_routing.py`, `tests/ci/test_recover_macos_runner.py` encode the lane labels and the runner-recovery behavior. The sysctl guard and the `bwrap` self-test step are workflow YAML with no dedicated test; they are exercised only by real CI runs (untested here).

## Applying

Apply the listed patches in order as part of the full ordered replay described in [README.md](README.md) (`git am -3 --empty=keep`); no extra configuration unless named above.

## Gotchas

This is fork-specific CI (an organization name and runner vendor are baked into the workflows); on another fork, re-run `fork-ci-runner.sh` with your own lanes rather than copying labels. An unavailable runner size queues forever with no error (the 8 vCPU case).

## Plugin

None.
