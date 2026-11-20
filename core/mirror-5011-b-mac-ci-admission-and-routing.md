# macOS CI admission and login-session routing

Mirror entries: 4505, 4525, 4526, 4527, 4528, 4529, 4530, 4531.

## Behavior

- **4505** — Shared check hooks admit structurally identified state candidates on macOS while refusing core, mixed and unknown trees.
- **4525** — Dedicated runner-root selection and runner-loss recovery support a configured macOS runner pool rather than a single runner.
- **4526** — Recovery workflow commentary describes the dedicated-runner pool consistently.
- **4527** — An intermediate package-manager change replaced datetime.UTC to support pre-3.11 bootstrap Python.
- **4528** — OS tests requiring a GUI launchd domain run in a separate login-session lane, not the non-GUI runner lane.
- **4529** — The package-manager datetime compatibility experiment is reverted; that compatibility is not provided by this pair.
- **4530** — Actionlint recognizes the login-session runner label and tests pin login-dependent coverage.
- **4531** — Routing tests exercise real file selection and pytest collection, replacing source-only marker inspection.

## How it works

On Darwin, shared-hook admission examines candidate Git objects, not checkout files, branch names or environment bypasses. It requires the expected regular state checker/guard/config files and refuses core markers. A recognized state layout still executes that candidate's checker and propagates failure. Non-Mac behavior remains unchanged.

Runner cleanup chooses exactly one allowed dedicated root using RUNNER_TEMP and refuses unlisted workspace shapes. Runner-loss recovery accepts only the dedicated-name allowlist and existing exact loss/failure conditions; malformed names refuse. Runtime labels, not the documentation, determine which machines take jobs.

The OS workflow excludes `macos_login_session` from its general platform lane and runs that selection separately on `hermes-mac-session`. A preflight requires the runner user's real `gui/<uid>` launchd domain and fails rather than silently skipping on misrouting. Strict-marker collection tests run the workflow's actual file selector and marker expressions, proving all intended login cases reach the session lane and none reach the general lane.

## Code

- `.github/actionlint.yaml`
- `.github/workflows/hermes-state-macos.yml`
- `.github/workflows/hermes-state-recovery.yml`
- `.github/workflows/macos-os-tests.yml`
- `.github/workflows/native-memory-macos.yml`
- `pm/network.py`
- `scripts/check`
- `scripts/ci/macos_os_tests_gate.py`
- `scripts/ci/recover_macos_runner.py`

## Tests

Source tests cover committed-tree admission versus misleading worktree files, incomplete/mixed/symlink/directory candidates and checker failure propagation; allowlisted cleanup roots and runner-loss guards; GUI preflight, actionlint labels and real strict-marker lane collection. The temporary Python bootstrap regression is removed by its revert. No native CI jobs, runner provisioning or bootstrap execution were performed by this doc-writing pass.

**Evidence boundary:** normalized patch bodies and their source tests were inspected for this document. Source tests and historical validation statements are not freshly executed verification results.

- `tests/ci/test_macos_checkout_retention.py`
- `tests/ci/test_macos_os_tests_workflow.py`
- `tests/ci/test_recover_macos_runner.py`
- `tests/conftest.py`
- `tests/hermes_cli/test_gateway_launchd_runtime.py`
- `tests/hermes_cli/test_gateway_snapshot_restore_live.py`
- `tests/hermes_cli/test_update_posix_pause.py`
- `tests/pm/test_network.py`
- `tests/scripts/test_check_mac_admission.py`
- `tests/scripts/test_check_state_admission.py`

## Applying

Use the complete segmented series recipe in [README — Applying](README.md#applying), including its ordered integration bridges and required post-replay repair/verification steps. These entries share context with the surrounding mirror and are **not independent patches**. Do not apply just the ordinals listed here as a standalone feature install.

## Gotchas

Layout recognition is not cryptographic authentication or permission to bypass checks. The session lane needs a configured console/login runner; workflow edits do not provision it or change runner labels. Do not claim pre-3.11 datetime compatibility from entries 4527/4529: their production and test edits cancel within this range. Public examples intentionally omit deployment-specific runner names and accounts.
