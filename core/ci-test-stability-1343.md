# PTY, runtime-install, and authorization CI stability

Patches: 1343 (`7e0d8f321aa`), 1344 (`9bd943bec32`), 1358 (`293b041480a`).

## Behavior

- The high-FD PTY test consumes the complete payload and CRLF before asserting
  an idle read waits, including a one-byte fragmentation case.
- Frontend CI includes a required runtime-only `tests-js` install-shape check
  for E2E drivers. Changes under `tests/install/` or `scripts/sandbox/`, plus
  icon-generation and driver-check inputs, select the frontend lane.
- Unauthorized-command E2E tests await background dispatch rather than sleeping
  300 ms, and verify both immediate and delayed pairing-worker schedules.

## How it works

PTY children wait on input until teardown instead of a fixed 30-second lifetime.
The test's output deadline becomes 30 seconds, then it checks idle waiting and
selector-creation failure handling with the existing fallback assertions.
The JS job installs locked runtime dependencies with `--omit=dev --ignore-scripts`,
resolves driver imports, and runs the bundled Windows helper test. Its checker
walks relative source imports and resolves bare packages without launching drivers.
Authorization tests join `adapter._background_tasks` with a ten-second timeout;
the negative case requires one pairing reply and no agent dispatch.

## Code

- `tests/tools/test_drain_high_fd.py`, `tests/e2e/test_platform_commands.py`.
- `.github/scripts/check-runtime-driver-imports.mjs`, `.github/workflows/js-tests.yml`.
- `scripts/ci/classify_changes.py`, `tests/ci/test_runtime_install_shape.py`.

## Tests

- `tests/tools/test_drain_high_fd.py::test_pty_bridge_reads_and_waits_above_fd_setsize`
  (`normal`, `fragmented`), POSIX only.
- In `tests/ci/test_runtime_install_shape.py`:
  `test_every_runtime_install_in_the_workflows_is_one_shape`,
  `test_job_installs_exactly_that_shape_and_gates_the_merge`,
  `test_checker_covers_every_node_script_the_launchers_run`,
  `test_every_input_of_the_shape_selects_the_frontend_lane`.
- In `tests/e2e/test_platform_commands.py::TestAuthorization`:
  `test_unauthorized_user_gets_pairing_response`,
  `test_unauthorized_user_does_not_get_help`, each with `immediate-pairing` and
  `delayed-pairing` (0.6-second delay) through the platform fixture.

Tests were inspected, not executed for this documentation task.

## Applying

Apply 1343, 1344, and 1358 in series order. No runtime configuration or plugin changes.
The JS CI lane must have the locked runtime-only install available.

## Gotchas

The import checker is a static string-literal scan, not proof every dynamic
runtime import works. Actual release-driver execution and the new CI job on a
runner are untested by the Python workflow-shape assertions.
