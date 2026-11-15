# CI and test-fixture fixes: native lane tools, home pinning, flock readiness, forkserver status

Mirror snapshot 4352; ordinals 4153, 4161, 4165, 4170, 4179, 4194, 4204.

Entries:

- **4153**: fix(ci): provision isolated native test tools and messaging
- **4161**: Fix Darwin-only fixture failures in failed-result runtime, socket and replay tests
- **4165**: Wait for the gateway lookalike to hold its flock, not for the lock file
- **4170**: pin the passwd account home in the PM runtime bundle tests
- **4179**: test: kill forked_exit_status join and _fork_lock mutants
- **4194**: worker policy: resolve the account scratch dir before admitting TMPDIR
- **4204**: Kill M2931d: race Process.start against the post-wait status read

## Behavior

Mostly test and CI changes; one production-path change.

- 4153: the native macOS test workflow provisions isolated locked tools (real uv, Node and npm, `all` plus `messaging` extras) before running tests; browser-launcher and PR-transport tests pin `Path.home` and enumerate only fixture-owned pids.
- 4161: Darwin-only fixture failures: failed-result runtime tests pin an instantiation epoch (with a negative test proving an empty epoch records no strict proof), the control-socket trickle test binds in a short temp root, and a replay test shares the bash prerequisite skip.
- 4165: the gateway-lookalike helper waits for a ready file touched after `flock` succeeds, rather than for the lock file.
- 4170: PM runtime bundle tests pin the account home from the password database.
- 4179, 4204: forked-exit-status tests kill join, `_fork_lock` and post-wait lock mutants by racing `Process.start` against the status read.
- 4194 (production): worker test policy compares `TMPDIR` against the realpath of the account scratch dir, so a symlinked account home still counts as ephemeral; the deny-by-default rules and "TMPDIR contains the account or HOME never joins" are unchanged.

## How it works

- `.github/workflows/macos-os-tests.yml`, `hermes_cli/worker_compute_capacity.py` (`_override_temp_roots`); the rest are test files listed below.

## Code

Shipped non-test paths (union over these entries):

- `.github/workflows/macos-os-tests.yml`
- `hermes_cli/worker_compute_capacity.py`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/ci/test_macos_os_tests_workflow.py`
- `tests/ci/test_update_ci_routing.py`
- `tests/gateway/test_control_socket_pause.py`
- `tests/hermes_cli/test_cross_profile_kill_refusal.py`
- `tests/hermes_cli/test_kanban_failed_result_runtime.py`
- `tests/hermes_cli/test_kanban_pr_acceptance.py`
- `tests/hermes_cli/test_kanban_pr_transport.py`
- `tests/hermes_cli/test_worker_compute_capacity.py`
- `tests/scripts/test_pm_runtime_bundle.py`
- `tests/scripts/test_pytest_exit_diagnostics.py`
- `tests/tools/test_browser_launcher.py`

## Applying

These entries are part of the complete ordered snapshot 4352. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 4352. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported. Some entries need no configuration; settings introduced are named under Behavior.

## Gotchas

- The workflow change affects only the native macOS lane. The scratch-dir rule now runs last in the OR chain because realpath reads the account home.

## Plugin?

None.
