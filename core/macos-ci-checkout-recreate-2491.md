# Recreate dedicated macOS CI checkouts at job start

Patches: **2457**.

## Behavior

The macOS `hermes_state` workflow (`.github/workflows/hermes-state-macos.yml`) runs on a dedicated self-hosted runner that keeps its checkout between jobs, so git objects accumulated and leftovers from cancelled or failed jobs persisted. The job now begins by deleting and recreating the runner's checkout directory (and an obsolete second checkout namespace) before `actions/checkout` runs, fetches with depth 1, and ends with an always-run step that measures the retained checkout and fails the job above 2 GiB. If any safety check fails, the job fails with a `RuntimeError` and deletes nothing.

## How it works

The first step, "Recreate dedicated CI checkout", runs a Python program (system `python3`, working directory `runner.temp`) whose `prepare()`:

- requires `GITHUB_ACTIONS=true`, `RUNNER_OS=macOS`, repository `example-org/hermes`, and the exact workspace and temp paths of the dedicated runner root, plus a cwd of runner temp and an fd-safe `shutil.rmtree`;
- validates every target before removing any: no symlink in the target or its ancestors, a directory on the runner root's device, not a mount point, no nested mount or foreign-device directory (walk without following links);
- runs `lsof -nP +D <target>` (120 s timeout) and requires exit status 1 with empty output (anything else, including a timeout or missing `lsof`, aborts);
- removes both targets, then recreates the workspace directory so it exists even if checkout fails.

The other runner directories (temp, actions, tool cache) are left alone. Related: [ci-macos-state-required.md](ci-macos-state-required.md) covers the macOS state lane itself.

## Code

`.github/workflows/hermes-state-macos.yml`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/ci/test_macos_checkout_retention.py` (extracts the real step program from the workflow YAML and runs `prepare()` against disposable temp trees: recreation after success/failure/cancel, busy or uncertain `lsof`, probe errors, environment allowlist, symlinks, cwd and rmtree safety, non-directory, mounts, foreign device, inner symlink, step wiring and the 2 GiB measurement step) and `tests/ci/test_macos_state_routing.py` (adjusted to select the test step by name). A real macOS runner execution, real `lsof`, and the measurement step's `du` are untested.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- The step depends on a single-job-at-a-time runner; it assumes no concurrent job uses these directories.
- The root and workspace paths are hard-coded for one runner layout; another layout fails the allowlist.
- The 2 GiB ceiling is enforced after tests, so an oversized checkout fails that run rather than being trimmed.

## Plugin

Core change; no plugin required.
