# Catalog CI isolation and deterministic regression fixtures

Covers **1428, 1438, 1439, 1440, 1441, 1449, 1450, 1451, 1452, 1455, 1468**, plus the sandbox startup-cancellation follow-up **1502** (last section).
Related CI changes are documented in [nightly E2E](nightly-e2e.md) (1430, 1432)
and [upstream-sync landing](upstream-sync-land-via-pr.md) (1442, 1443, 1463).

**Verification boundary:** actual patch implementations and source test coverage were inspected. Source tests were **not rerun in this mirror pass**. No workflow, sandbox, runner deployment, scheduler or gateway was exercised for this documentation. Test descriptions below are contracts in source, not passing-result claims.

## Behavior

### Catalog admission and trusted tooling — 1438, 1439

1438 makes catalog diffs tree-only with `--no-renames`, avoiding rename detection's demand for old blobs after checkout credentials have been removed. A rename is seen as deletion plus addition. The workflow also triggers when its own YAML changes. Mixed code/catalog changes can be admitted by upstream content provenance rather than a branch-name exception.

1439 tightens that admission rule and separates PR data from executable tooling:

- Both jobs export catalog bytes from the PR merge tree as regular files, remove the PR checkout, and check out its first parent (the trusted base). Structural validation, composite actions, core build, sandbox and payload scanner then come from the base. Missing trusted tools fail closed rather than falling back to PR copies.
- For mixed changes, the PR must advance its upstream merge-base. Every changed entry must be an actual upstream change to an entry the fork had not modified: base entry equals old upstream entry, resulting entry equals new upstream entry, and upstream entries differ. Comparisons include mode and object ID. This rejects restoring fork-deleted entries and replacing fork-local pins, including inside a real upstream sync. Non-catalog conflict resolutions are not otherwise constrained by this test; their files are not executed by this gate.
- Each admitted plugin is still checked at its pinned commit. Plugin validation with dependency installation runs only inside the sandbox. Missing isolation is a failure, not a host-execution fallback. The attestation credential remains outside plugin execution.

### Sandbox and cleanup — 1439, 1440, 1441, 1449, 1450, 1451

1439 adds a Linux bubblewrap wrapper with a cleared environment, private namespaces and process view, a minimal filesystem, read-only checkout/clone mounts, and a disposable writable PM-home view. Credential-like environment names, credential-store binds and runner file-command paths are refused. Dependency egress is through a CONNECT proxy restricted by default to public destinations on port 443, not the host network.

1440 binds the proxy socket through its opened parent-directory fd, so deeply nested temporary paths do not overflow the Unix socket address. A symlinked final parent directory is refused; the socket remains in private scratch with mode 0600.

1441 introduces pidfd-based teardown and suppresses repeated cancellation signals during cleanup. **It is not the final normal-exit guarantee:** 1449 adds a regression for normal command exit while an orphaned writer remains; 1450 replaces the monitor-only teardown with a dedicated subreaper that acknowledges full descendant quiescence before scratch deletion. Missing acknowledgment retains scratch. 1451 adds SIGINT alongside SIGTERM coverage and makes the missing-ack control independent of an installed bubblewrap binary.

### Test-only determinism — 1428, 1452, 1455, 1468

- 1428 changes the managed-worker test's exit observer, not production gateway code. A zombie or a `psutil.NoSuchProcess` status race counts as exited; `AccessDenied` still propagates.
- 1452 freezes the profile-timezone test's clock and pins the next scheduled instant for winter, summer, spring-forward and fall-back. The next occurrence's offset, not the current day's offset, is the oracle; real profile-scoped timezone lookup remains in use.
- 1455 counts complete recorded `du` invocations rather than occurrences of `-sm` in their text. A fixture path deliberately contains `-sm` and spaces, preserving the assertion that only the sparse tree needs sizing.
- 1468 freezes the quota-guard clock before task creation and lifecycle writes. Authentication escalation and non-escalating quota holds then share a deterministic timeline even when claims are slow. No quota or authentication runtime policy changes here.

## How it works

### Trusted-base catalog jobs

The structural job checks for a PR merge commit, enumerates `plugin-catalog/` with NUL-delimited tree records, exports regular blobs outside the checkout and rejects symlinks/submodules. The pinned-source job exports candidate blobs similarly. Before executing workspace tools, each job verifies the checked-out SHA, a clean workspace including ignored/untracked files, and its explicit trusted-tool list.

The mixed-change check reads outside-data paths NUL-delimited and compares literal tree paths without fetching old blobs. It fetches upstream anonymously, requires one unambiguous merge-base for both the base and merge head, and verifies a forward ancestry relationship. Unavailable or ambiguous upstream evidence fails closed. Candidate URL/subdirectory checks, exact-pin checkout, attestation and payload checks remain in the validation path.

### Sandbox boundary

`plugin_validation_sandbox.py` launches bubblewrap with `--unshare-all`, explicit user isolation, disabled nested user namespaces, dropped capabilities and a fresh environment. Its inner entry checks that the user/PID/network/mount/IPC/UTS namespace identities differ from the host before starting the command. Setup and self-check failures use exits 125 and 126; bubblewrap itself can also fail nonzero.

Read-only mounts contain the base checkout and cloned plugin. Writable views use kernel overlay support when available or a per-run `cp -a --reflink=auto` copy otherwise. The PM home is therefore not persistently modified by plugin dependency installation. The host proxy resolves destinations, rejects any non-public answer (including embedded IPv4 forms), dials the checked numeric address without a second DNS lookup, and rejects self-connections. Domain restrictions are optional; the default allows public hosts, not only package registries. `--no-network` omits the proxy.

1440's `_bind_unix` opens the socket parent with `O_PATH | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC` and binds/chmods via `/proc/self/fd/<fd>/<name>`. It avoids moving the socket into shared short-path storage.

1450's `run_supervisor` is a separate isolated-Python child with `PR_SET_CHILD_SUBREAPER` and a parent-death signal. It owns only that bubblewrap tree, not the importing caller's other children. Cancellation is blocked across supervisor creation to avoid losing a just-spawned child. After the monitor exits, `_reap_sandbox_children` repeatedly reaps adopted children, using pidfds for kill targets, until `waitpid` reports ECHILD. Only then does the supervisor write `Q` on an inherited pipe that never reaches bubblewrap or plugin code. `_finish_supervisor` bounds shutdown; absent `Q` prevents scratch deletion and the normal return becomes setup failure. Signal-driven exits retain the signal exit path while cleanup runs. An empty procfs snapshot alone is not quiescence proof.

### Test fixtures

1428 introduces `_background_exited` in the existing managed-worker launch test and imports it into a focused race test. 1452 substitutes a `datetime.now` implementation only for `hermes_time`, with explicit next-run timestamps and unchanged timezone resolution. 1455 asserts the entire `du` log's line list. 1468 sets `kb.time.time` to a fixed timestamp before writes, then advances it through guard intervals.

## Code

| Ordinals | Changed implementation or fixture paths |
|---|---|
| 1428 | `tests/gateway/test_managed_worker_launch.py`, `tests/gateway/test_managed_worker_exit_poll.py` |
| 1438, 1439 | `.github/workflows/plugin-catalog-ci.yml`, `tests/ci/test_plugin_catalog_ci_confinement.py`; 1439 also `tests/ci/test_fast_runner_routing.py` |
| 1439, 1440, 1441, 1450, 1451 | `scripts/ci/plugin_validation_sandbox.py`, `tests/ci/test_plugin_validation_sandbox.py` (1450's added tests are in the lifecycle file below) |
| 1449, 1450 | `.github/workflows/sandbox-lifecycle-proof.yml` |
| 1449 | `tests/ci/test_plugin_sandbox_normal_exit.py` |
| 1450, 1451 | `tests/ci/test_plugin_sandbox_lifecycle.py` |
| 1452 | `tests/cron/test_cron_next_run_profile_timezone.py` |
| 1455 | `tests/cron/test_worktree_maintenance_once_per_repo.py` |
| 1468 | `tests/hermes_cli/test_kanban_quota_guard.py` |

## Tests

Coverage below is present in source; **not rerun here**.

### Catalog and isolation

- 1438 `tests/ci/test_plugin_catalog_ci_confinement.py`: `test_blobless_checkout_of_a_private_fork_needs_no_credentials` exercises actual workflow shell against a blobless clone with an unreachable promisor; valid upstream-sync, hostile mixed-admission variants and unreachable upstream cases cover content/mode admission and fail-closed behavior.
- 1439 expands that suite with fork-deleted entry restoration, fork-local pin overwrite/revert, upstream changes including tooling, non-catalog conflict resolutions, typechanged entries, and real-history fixtures. Trusted-base cases inject alternate actions/core/scanner/validator files and include controls showing the injected validator/scanner can run without the trusted checkout. Bootstrap cases reject an entry on a base missing the sandbox and allow the tooling-only introduction. `test_validate_runs_only_inside_the_sandbox`, `test_a_sandbox_that_cannot_be_built_fails_the_entry`, the unisolated leak control and `test_plugin_code_in_the_real_step_sees_no_token_and_cannot_write` exercise the workflow validation entry.
- 1439 `tests/ci/test_fast_runner_routing.py::test_plugin_catalog_sandbox_runs_on_the_runner_ami_without_host_setup` checks workflow routing and preinstalled-isolation assumptions; it is not an image deployment test.
- 1439 `tests/ci/test_plugin_validation_sandbox.py`: missing/broken/fake bubblewrap setup and self-check refusals; forbidden environment/path/git-credential binds; unreadable secret paths; private/loopback/metadata and IPv4-embedding egress policy; host/self-connection controls; domain suffix matching; unlistable scratch cleanup; host-process/token/file invisibility; descendant termination and timeout; copy/kernel overlay immutability; public HTTPS downloads; and `hermes plugins validate --install-deps` under isolation. Namespace, network and real-Hermes tests depend on their fixture prerequisites; their presence is not evidence those prerequisites were satisfied in this mirror pass.
- 1440 adds `test_the_proxy_binds_and_serves_under_a_deep_runner_temp` and `test_the_proxy_socket_is_never_bound_through_a_symlinked_dir` to that suite.

### Lifecycle ordering

- 1441 adds `test_scratch_is_removed_only_after_every_sandboxed_process_exits`, initially with wrapper and external-timeout launch modes, plus failed-SIGKILL cleanup controls.
- 1449 `test_normal_exit_quiesces_namespace_before_remove_tree` in `tests/ci/test_plugin_sandbox_normal_exit.py` runs repeated normal exits with a detached writer and records FIFO reader/namespace/file state at the actual scratch-removal entry. The workflow initially ran this against the prior implementation; 1450 changes it to the current regression and lifecycle suites with file retries disabled. This documentation did not dispatch it.
- 1450 `tests/ci/test_plugin_sandbox_lifecycle.py`: `test_normal_exit_waits_for_an_orphaned_writer` uses a fake bubblewrap executable with real orphan processes and checks command status preservation plus no writer at removal; `test_missing_quiescence_ack_retains_scratch` substitutes a real child exiting without the acknowledgment and checks exit 125 plus retained scratch; `test_supervisor_leaves_callers_other_children_and_subreaper_state_alone` checks sibling and caller isolation.
- 1451 parameterizes the namespace teardown regression for SIGINT and SIGTERM under both wrapper and timeout launch modes. The missing-ack control explicitly selects `/bin/false` as its bubblewrap path, so missing bubblewrap cannot satisfy the test via an unrelated early failure.

### Deterministic test-only follow-ups

- 1428 `tests/gateway/test_managed_worker_exit_poll.py`: `test_background_exit_poll_reaped_during_status` kills/reaps a real child exactly at its status read; `test_background_exit_poll_zombie_missing_and_access_denied` covers live, zombie, missing and denied reads. These tests are Linux-only.
- 1452 `tests/cron/test_cron_next_run_profile_timezone.py::test_foreign_process_tick_persists_owning_profile_offset`: winter/summer and both DST transitions assert exact next-run timestamps and 14:00 local wall time with a foreign process home.
- 1455 `tests/cron/test_worktree_maintenance_once_per_repo.py::test_footprint_warning_sizes_the_tree_only_when_the_count_does_not_decide`: a crowded tree bypasses sizing; a sparse path containing the flag text yields exactly one recorded invocation and the expected size warning.
- 1468 `tests/hermes_cli/test_kanban_quota_guard.py`: `test_real_auth_error_still_blocker_auth_and_escalates` and the outcome-parameterized `test_quota_hold_never_escalates_to_human_block` retain their existing guard assertions with the clock frozen before lifecycle writes.

## Applying

Use [README.md](README.md)'s complete numbered, bridge-aware replay procedure. These are dependent follow-ups, not an independently applicable selection. In particular keep 1438 → 1439 → 1440 → 1441 → 1449 → 1450 → 1451 in their full-series order; stopping at 1441 omits the normal-exit fix. Earlier fixture/runtime patches are required for the test-only changes.

Catalog validation needs a Linux runner with bubblewrap and permitted unprivileged namespaces already provisioned. The workflow proves isolation before validating plugins; it does not install or reconfigure host isolation. Entry PRs need a base containing the trusted tools. The lifecycle-proof workflow is dispatchable and also selects its dedicated proof-branch pattern; 1450 removes the old implementation override. No workflow or runner was deployed by this mirror documentation task.

## Gotchas

- The workflow definition itself still comes from the PR on `pull_request`; trusted-base execution does **not** make a malicious workflow edit safe. Workflow review remains part of the trust boundary.
- Namespace isolation is not protection against a kernel exploit. Plugin stdout/stderr still reaches the Actions log. Public HTTPS egress is permitted unless narrowed; this is not a general no-exfiltration claim for arbitrary mounted data.
- Bind policy is restrictive: use an isolated PM home, not a live credential-bearing home. Copy fallback costs disk/time and is discarded per run.
- Scratch retention on missing quiescence is intentional. Do not remove it merely because the monitor exited; confirm descendant termination first.
- Deep-path binding relies on Linux procfs and directory-fd semantics. The wrapper is Linux-only; no macOS/Windows sandbox claim is made.
- The gateway, cron and quota follow-ups harden test oracles only. They do not add production process-reaping behavior, change schedule policy or alter quota escalation policy.

## Plugin?

No. These are core repository CI workflows, their sandbox helper and regression tests. The sandbox executes third-party plugin validation but is not itself an installable agent plugin.

## Follow-up patch (1502): startup-cancellation regression

**1502** (`ac1d014219`, `test(ci): checkpoint cancellation before sandbox init setup (#460)`). Test plus workflow only; `scripts/ci/plugin_validation_sandbox.py` is unchanged.

- **Behavior covered.** A run cancelled (SIGTERM or SIGINT) during sandbox startup must not let a cloned PID-namespace init escape teardown. In the race, the launcher is stopped before the bubblewrap `exec`, and the caller's `kill()` is stopped after the old descendant scan. Real bubblewrap then clones the PID namespace and blocks before `do_init`'s parent-death signal. The test requires exit status `128 + signum`, a namespace init that is already dead when `remove_tree` is entered, the init reaped by the time `run_host` returns, and no `plugin-sandbox-*` scratch left behind.
- **How it works.** `tests/ci/test_plugin_sandbox_startup.py` (new, `platforms("linux")`, `needs_isolation`) runs the real sandbox module through a probe script. It wraps `subprocess.Popen` to insert an observation-only checkpoint into the `--supervise` child, and SIGSTOPs the killer inside `Popen.kill` for the gate. It also wraps `remove_tree` to record, via a pidfd, whether init is dead. A gate executable stands in for `--bwrap`: it SIGSTOPs itself, then `exec`s `/usr/bin/bwrap` with `--block-fd` on a FIFO and `--info-fd` so that the test learns the init pid. The docstring states that no sandbox lifecycle operation is mocked or replaced. Cleanup SIGKILLs init through the pidfd and SIGCONTs the stopped pids, so a failing run leaves nothing stopped.
- **Workflow.** `.github/workflows/sandbox-lifecycle-proof.yml` adds the new file ahead of the normal-exit, lifecycle and validation suites, with `HERMES_TEST_FILE_RETRIES=0`.
- **Tests.** `test_startup_cancel_reaps_init_before_cleanup[SIGTERM|SIGINT]`. It needs non-root execution (`os.getuid() != 0` is asserted), `/usr/bin/bwrap` and unprivileged namespaces. It was **not run for this mirror**, and the workflow was not dispatched. The patch does not say whether this test was red on the base before an implementation fix; read it as a regression guard for the 1450 subreaper behavior, not as evidence that a fix was made here.
- **Gotchas.** The checkpoint depends on procfs details (`/proc/<pid>/stat` state `T`, `/proc/<pid>/wchan` reading `pipe_read`/`anon_pipe_read`). On a kernel that reports a different wait channel, the test hangs until its 30 s `wait_until` timeout instead of failing on the race itself.

## Follow-up patch (1565): fenced startup-proof cleanup and acknowledged disarming

**1565** (`60f9d980c4`, `test(ci): fence startup checkpoint cleanup and acknowledge disarming`). Test fixture plus workflow only; `scripts/ci/plugin_validation_sandbox.py` is unchanged.

- **Behavior.** 1502's teardown SIGKILLed init through a pidfd and SIGCONTed only the PIDs it had discovered. A checkpoint that had not yet published its PID, or a stop that a SIGCONT reached before the SIGSTOP, could stay stopped. 1565 moves cleanup into an out-of-process ownership fence and a lock-acknowledged checkpoint protocol. The fence does nothing until the test's own assertions have run, so cleanup cannot make the intentionally failing baseline pass.
- **How it works.** New `tests/ci/sandbox_startup_fixture.py`:
  - `checkpoint(control, name)` holds `<name>.lock` (`flock`). It returns `False` if a `disarmed` file exists. Otherwise it publishes `[pid, start-time]` atomically to `<name>.identity`, then SIGSTOPs itself while still holding the lock.
  - `disarm(control)` creates `disarmed`. For each checkpoint (`monitor`, `killer`) it then retries a non-blocking lock until it is acquired, sending SIGCONT through a pidfd each time the lock is busy, and only if `/proc/<pid>/stat` still shows the published start time. Acquiring the lock is the acknowledgment; a single SIGCONT is not.
  - `FencedProcess` starts the command under `python -I sandbox_startup_fixture.py`. That process makes itself `PR_SET_CHILD_SUBREAPER`, forwards signals written on its stdin, and publishes `wrapper` and `status`. On stdin EOF, `_reap_owned` SIGKILLs every child in `/proc/self/task/<pid>/children` and reaps until `waitpid` raises ECHILD, then publishes `reaped`. `close()` disarms, closes stdin, and asserts that the fence exited 0 and published `reaped`.
- In `tests/ci/test_plugin_sandbox_startup.py`, the probe's monitor and killer stops now go through `checkpoint`, and a disarmed monitor exits 0 without exec'ing bubblewrap. Both tests launch through `FencedProcess`.
- **Workflow.** `.github/workflows/sandbox-lifecycle-proof.yml` runs `tests/ci/test_sandbox_startup_fixture.py` first, ahead of the existing four suites (still `HERMES_TEST_FILE_RETRIES=0`).
- **Tests** (in the patch; not run for this mirror, workflow not dispatched):
  - `tests/ci/test_sandbox_startup_fixture.py::test_disarm_acknowledges_checkpoint_without_discovered_pid[monitor|killer × before_entry|publication_gap|stopped]`. No PID is passed to `disarm`. `publication_gap` sends the first SIGCONT before the SIGSTOP and asserts at least two continues. Acknowledgment is asserted before fence cleanup.
  - `test_fence_reaps_unknown_stopped_session_and_late_child`: a tree that ignores the protocol (`setsid`, cleared environment, stopped grandchild after the wrapper exits) is untouched until `close()` and dead afterwards.
  - `tests/ci/test_plugin_sandbox_startup.py::test_checkpoint_timeout_reaps_real_sandbox[before_monitor|before_killer|unknown_init]` injects a controller timeout at each phase without handing any PID to cleanup. It then checks that no published wrapper, monitor, killer or init PID survives. It needs non-root execution with real bubblewrap (`needs_isolation`).
- **Gotchas.** Linux-only. The fixture relies on procfs field 22 (start time), `/proc/self/task/<pid>/children`, `os.pidfd_open` and a `prctl` call through `ctypes`. The fence assumes it is single-threaded so that unreaped child PIDs cannot be reused. Production sandbox teardown (1450's subreaper) is not changed or re-proved by this patch.
