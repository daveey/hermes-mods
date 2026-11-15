# Test harness stability: bootstrap, wrappers, forkserver and runner sweeps

Mirror snapshot 4142; ordinals 4077, 4105, 4106, 4107, 4121, 4127, 4128.

## Behavior

These entries harden the test suite and test runner against races seen as intermittent CI failures. Almost all of the change is in tests and fixtures; two small production-path edits are in the test runner scripts and the scratch-pruning helper.

- A desktop-bootstrap test no longer treats "worker thread created" as "bootstrap record published". It waits for the record asynchronously, and runs both with normal scheduling and with the worker held back.
- A wrapper-process test reads each child's argv once, so a child that is mid-exec cannot cause an `IndexError`.
- The test runner's forked-pytest helper keeps a forkserver child's exit status even after the status bytes were already consumed while the server still holds its pipe writer open.
- A Kanban gateway restart probe waits for the replacement gateway owner started by the real worker client, instead of racing it with a second launcher.
- Extra tests cover the shutdown-diagnostic watchdog: it keeps dumping stacks when a session-finish hook blocks, with and without active stderr capture, and does not terminate the hung process.
- The runner's startup sweep of old run roots no longer reaps processes whose working directory was deleted inside a fresh test attempt. The default behavior of the shared pruner (reap deleted-cwd processes) is unchanged.

No entry path (terminal, execute_code, tool, REST, Desktop) is claimed as a product behavior change; the desktop test in 4077 exercises the in-process gateway request path only.

## How it works

- 4077: the test `test_the_first_local_desktop_connection_runs_the_desktop_boot_work` is parametrized with `delayed_start` False/True. Bootstrap worker threads are spawned through a tracking wrapper; in the delayed case the `free-tier-bootstrap` worker blocks on an event that is released on the next event-loop turn via `call_soon`. The assertion on the record uses `async_wait_until(free_tier_bootstrap.current_record, timeout=20, ...)` rather than the synchronous `wait_for_record`, which returns immediately until the bootstrap `_started` flag is set. Owned threads are joined before globals are reset. Due-computation startup is stubbed for this test only.
- 4105: a helper `_children_by_script` calls `child.cmdline()` once per child and filters on that snapshot (length at least 2), replacing a comprehension that checked one read and indexed another.
- 4106: `forked_exit_status` in `scripts/run_tests_launch.py` first reads the multiprocessing process `exitcode` under `_fork_lock`; if a status is already cached it joins the process (so the child registry is cleaned) and returns it. Only then does it fall back to waiting on the sentinel. This covers the case where the sentinel is empty and not readable because the status was consumed but the writer is still open.
- 4107: the restart probe fixture gains `restarted_owner`, which polls the control socket `identify` call until the state is `ready`, asserting the pid and instance id differ from the first owner. The fixture no longer starts a second raw gateway launcher. `stop_descendants` terminates, then kills, captured descendant processes, and the submitter is stopped before those descendants so teardown cannot relaunch an owner. The first launch identity (`first['pid'] == proc.pid`) is now asserted.
- 4121: a new parametrized test (`sessionfinish`, `captured-stderr`, `repeat`) writes a conftest whose session-finish wrapper blocks forever after the diagnostics hook, starts pytest through the runner launch helper, and reads its output.
- 4127: `reap_processes_rooted_in` and `prune_idle_entries` in `hermes_constants_scratch.py` gain a keyword-only `reap_deleted_cwds` (default `True`). When false, only processes inside entries selected for deletion are reaped, and the post-rescue re-filter drops the deleted-cwd clause. `_sweep_killed_run_roots` in `scripts/run_tests_parallel.py` passes `False`.
- 4128: the sweep regression sets `HERMES_TEST_POLICY_ROOT` so the fresh holder interpreter uses the same policy isolation as the parent test process.

## Code

Source paths touched by this feature group (including regression fixtures):

- `hermes_constants_scratch.py`
- `scripts/run_tests_launch.py`
- `scripts/run_tests_parallel.py`
- `tests/gateway/fixtures/kanban_restart_continue_probe.py`
- `tests/gateway/test_kanban_restart_probe_owner.py`
- `tests/gateway/test_local_desktop_identity.py`
- `tests/hermes_cli/test_update_checkpoint_wrapper_pid.py`
- `tests/scripts/test_pytest_exit_diagnostics.py`
- `tests/scripts/test_runner_sweep_live_holder.py`

Mail entries in the ordered series:

- 4077: [`abc69469ded37a11dc9d4044f878dbd626638afc`](patches/4077-daveey-test-await-desktop-bootstrap-publication-befo.patch)
- 4105: [`82a185ce41cbd484b65defd9154c1a8c8f97cb36`](patches/4105-daveey-fix-tests-snapshot-wrapper-child-argv-once.patch)
- 4106: [`43b46576e681fced0e3a37a68fa8e7aede2a9187`](patches/4106-daveey-retain-forkserver-exit-status-after-sentinel-.patch)
- 4107: [`7d2b02bd77923d7036c7e3c639414a023041c4e1`](patches/4107-daveey-Observe-the-submitter-started-owner-in-the-Ka.patch)
- 4121: [`38c2caf512ee801a29c2bf0d7824e5f02e94ef11`](patches/4121-daveey-Cover-shutdown-diagnostic-watchdog-mutation-g.patch)
- 4127: [`0b5ea7fa4456353df5f267235a7f0755edc5b3d8`](patches/4127-daveey-Keep-runner-startup-sweeps-out-of-fresh-test-.patch)
- 4128: [`db7f8153136c39d48ceecb1f7a24dddf0a3d0e99`](patches/4128-daveey-Isolate-holder-subprocess-worker-policy-in-sw.patch)

## Tests

- `tests/gateway/test_local_desktop_identity.py::test_the_first_local_desktop_connection_runs_the_desktop_boot_work[False/True]` (4077): after two `client.capabilities` requests, the setup record eventually appears within 20 seconds, the first-install latch ran once on the local desktop backend, and owned worker threads finish. In the delayed case the test first asserts bootstrap has not started.
- `tests/hermes_cli/test_update_checkpoint_wrapper_pid.py::test_child_discovery_uses_one_argv_snapshot` (4105): with mocked children whose second `cmdline()` read would be empty, discovery returns only the two valid scripts, and each child's `cmdline` is called exactly once. The `wrapper_tree` fixture now uses the helper.
- `tests/scripts/test_pytest_exit_diagnostics.py::test_forked_status_survives_consumed_sentinel` (4106; statuses 0, 1, -9, polling first or not; Linux only): with a real OS pipe and multiprocessing's forkserver status decoder, writer held open, a live child polls `None` and times out on `communicate` while keeping its output file; after the status is written and consumed, `poll()` and `returncode` keep the status, the output file is removed, and `kill()` does not signal.
- `tests/gateway/test_kanban_restart_probe_owner.py` (4107, new file): `test_replacement_waits_for_identified_ready_owner` asserts `restarted_owner` retries through a socket error and a `starting` state and returns the ready descriptor after three `identify` calls; `test_replacement_rejects_previous_owner_identity` asserts an `AssertionError` when the ready owner reuses the previous pid or instance id. The probe itself (`kanban_restart_continue_probe.py`) is a fixture; its end-to-end run is not a listed test function here.
- `tests/scripts/test_pytest_exit_diagnostics.py::test_sessionfinish_watchdog_survives_blocked_hook[sessionfinish|captured-stderr|repeat]` (4121): a blocked session-finish hook yields the "blocked-hook-enter" marker and at least one (two for `repeat`) watchdog `Timeout (` stack dump naming `blocked_sessionfinish`; the process is still alive when the dumps are seen; `unconfigure-enter` is absent; and stderr written while capture was active does not appear in the output.
- `tests/scripts/test_runner_sweep_live_holder.py::test_runner_sweep_deleted_cwd_scope[fresh-runner|aged-runner|default-pruner]` (4127, adjusted in 4128; Linux only): a holder whose cwd tree was deleted survives the runner sweep on a fresh root and is later killed by the slot reclaimer; with an aged root, or with the default pruner, it is terminated with SIGTERM. The aged-runner case also checks the root was removed only by the default pruner. 4128 only adds the `HERMES_TEST_POLICY_ROOT` environment line.
- `tests/scripts/test_runner_sweep_live_holder.py::test_runner_scope_keeps_deleted_child_when_idle_entry_is_rescued` (4127): with `reap_deleted_cwds=False`, a deleted-cwd child is not terminated, the rescued entry is dropped from the doomed list, and the count returned is 0.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks.

## Gotchas

- Entries 4077, 4105, 4107, 4121 and 4128 change tests or fixtures only; 4106 and 4127 change runner/helper code paths used by the suite.
- `reap_deleted_cwds` defaults to `True`, so other callers of the pruner keep reaping deleted-cwd processes. Only the runner's startup sweep opts out.
- Several new tests are marked Linux-only (`platforms("linux")`) or depend on `/proc` and `psutil`.
- The 4107 fixture still has no dedicated unit test for its stop/teardown ordering; only `restarted_owner` is unit tested.
- Retry and timeout policy are unchanged by these entries.
- Related diagnostics doc: [pytest exit diagnostics](mirror-4059-f-pytest-exit-diagnostics.md).

## Plugin?

No. These are changes to the core repository's own test suite, runner scripts and scratch-pruning helper; a plugin cannot alter them.
