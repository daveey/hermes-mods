# Locked ripgrep in reusable test jobs

Patch **2051**.

## Behavior

Reusable Python, core E2E and upgrade test jobs obtain ripgrep from the repository's package-manager setup instead of independently downloading a release tarball and moving it into `/usr/local/bin` with sudo. The upgrade lane also obtains locked Node through that setup.

## How it works

The relevant `setup-pm` action invocations request `packages: ripgrep`; Python/core E2E retain `toolchain: all`, while the upgrade lane uses `toolchain: node` in place of `actions/setup-node`. Explicit `rg --version` steps remain in the latter two lanes. Package caching and checksum verification belong to the existing PM action/lock infrastructure.

## Code

`.github/workflows/tests.yml` only.

## Tests

**Not rerun this pass.** No test file is added or changed. Actual entry paths are the reusable workflow's Python tests, core E2E and upgrade jobs; their setup and `rg --version` checks require CI execution. This documentation pass does not establish successful provisioning or a green workflow run.

## Applying

Apply 2051 through [the core replay instructions](README.md), after the repository's existing `setup-pm` action and package lock support. Do not treat the workflow diff as a self-contained installer.

## Gotchas

The change removes duplicated release-download logic, not the need for package availability/network access on a cold cache. It does not remove unrelated tmux or uv setup.

## Plugin eligibility

Not applicable: GitHub Actions provisioning is repository CI configuration, outside agent-plugin execution.
