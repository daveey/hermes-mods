# CI, runner and regression-test hardening

This document covers **only 1303, 1304, 1306, 1307, 1308, 1309, 1317, 1320, 1321 and 1322**. Each entry describes its actual source diff, not everything advertised by its commit subject.

**Verification boundary:** source tests inspected, not executed in this mirror pass. Assertions described below are source contracts, not newly observed passing results. No cloud image was built, runner deployed, workflow dispatched or application launched for this documentation.

**Applying the patches:** apply the whole numbered series, in order, at the fork merge-base for the frozen snapshot, using `git am -3 --empty=keep`. These are not standalone patches or a ten-patch independent bundle. Earlier patches supply their implementations, fixtures and workflow infrastructure. Use the mirror inventory and verification policy to identify the correct merge-base; do not start at a newer upstream tip. Per-entry Applying notes below describe integration requirements, not a substitute application order.

## 1303 — Keep the interrupted-backup test inside its lock barrier

Source: `7f5538e27a26a415b8bcf0057e438389fa52e61d`.

### Behavior

Makes the lock-blocked, SIGTERM-mid-copy preflight regression test less sensitive to runner starvation. The ordinary SQLite copy lock-wait default remains 10 seconds; this test asks for 600 seconds so its child is still copying when the parent signals it.

### How it works

`preflight_state_db` gains the keyword-only `busy_timeout_seconds=10.0` argument and passes it to `_safe_copy_db` as `timeout_seconds`. This revision's standalone entry reads a timeout override from the environment. The test sets that override in the subprocess environment. This changes the barrier duration, not the expected signal result or cleanup assertions.

### Code

- `hermes_cli/backup_sqlite.py`: `preflight_state_db` and the standalone `__main__` entry.
- `tests/hermes_cli/test_backup_preflight_staging.py`: locked-source subprocess setup.

### Tests

Source tests inspected, not executed in this mirror pass:

- `tests/hermes_cli/test_backup_preflight_staging.py::test_sigterm_mid_copy_removes_the_staging_copy`: takes an exclusive rollback-journal lock, waits for staging while the child remains alive, sends SIGTERM, and asserts exit `128 + SIGTERM`, empty stdout and no staging files. Only the timeout setup changes here.

### Applying

Use the whole-series procedure above. Keep 1304: it immediately replaces this transient environment interface with an optional positional argument.

### Gotchas

This is not a change to the Desktop's preflight deadline, nor a new SIGTERM cleanup implementation. Cleanup already exists. A longer barrier does not guarantee scheduling within the parent's separate 30-second staging wait.

### Plugin?

No. This is core backup plumbing and its subprocess regression test.

## 1304 — Pass the preflight lock wait by argument

Source: `49f25091b12cb891a4c62f30359f19929efdecfd`.

### Behavior

The standalone backup script accepts an optional second argument after the home directory for its SQLite busy timeout. It defaults to 10 seconds. The interruption test passes `600` directly rather than relying on an environment override.

### How it works

The entry computes `float(sys.argv[2])` when present, otherwise `10.0`, and forwards it through the keyword introduced by 1303. The child is still started with isolated Python flags `-I -S`.

### Code

- `hermes_cli/backup_sqlite.py`: standalone timeout parsing.
- `tests/hermes_cli/test_backup_preflight_staging.py`: subprocess argv.

### Tests

Source tests inspected, not executed in this mirror pass:

- `tests/hermes_cli/test_backup_preflight_staging.py::test_sigterm_mid_copy_removes_the_staging_copy`: exercises the actual standalone script with the `600` argument and retains the signal-exit and staging-cleanup assertions described under 1303.

There is no new dedicated test here for malformed timeout arguments or the unchanged default value.

### Applying

Use the whole-series procedure above; this entry depends on 1303's function signature and forwarding.

### Gotchas

The environment timeout interface from 1303 is no longer read after this patch. The optional value is parsed as a float without new validation or a new public CLI subcommand.

### Plugin?

No. This belongs to the standalone core preflight script and its tests.

## 1306 — Pin forwarded owner-archive entry behavior

Source: `5be6ef307908922cd408850314c1c097962d3223`.

### Behavior

Adds regression coverage for processing a forwarded capability-owner archive when remote stop requests fail, and for preserving requester/run attribution through the worker archive entry. **This source diff changes tests only.** It does not itself perform the refactoring or code-health-cap restoration described in the commit message.

### How it works

Two temporary board connections model home and worker gateways. A completed owner card exists only at home. The first test forwards an archive without waiting, injects a failure into `apply_stop_requests`, copies the request event home, and invokes `apply_remote_archives`. The second calls `worker_archive` with a recording run-id callback and checks the resulting home archive event.

### Code

- Changed: `tests/tools/test_kanban_owner_archive_relay.py` only.
- Existing implementation seams exercised: `hermes_cli.kanban_owner_archive_relay.apply_remote_archives`, `forward`, `relayed_owner_route` and `worker_archive`.

### Tests

Source tests inspected, not executed in this mirror pass:

- `tests/tools/test_kanban_owner_archive_relay.py::test_dispatcher_step_applies_forwarded_archive_when_stop_requests_fail`: asserts pending forwarding, eventual archived status despite the injected stop failure, an empty second application and exactly one result event.
- `tests/tools/test_kanban_owner_archive_relay.py::test_worker_archive_entry_forwards_done_owner_with_requesting_run`: asserts no refusal, an archived result, run-id lookup for the requesting task, and matching `forwarded_task`, `forwarded_run_id` and `via` attribution in the home event.

### Applying

Use the whole-series procedure above. Preserve the earlier relay implementation and fixture helpers; these tests are not an implementation replacement.

### Gotchas

The dispatcher-named test calls its archive-processing helper, not a complete dispatcher loop. Gateway event transport is simulated by copying board events, not tested over a live network.

### Plugin?

No implementation is added. These are core Kanban integration regression tests.

## 1307 — Require all eight upstream-sync Python slices

Source: `0a91e5a5cd2c6a7a394cc3fe715c420b5e1ff6a1`.

### Behavior

The upstream-sync full-suite workflow requests eight Python slices instead of two. Its landing gate now requires successful evidence for `Run tests (1/8)` through `Run tests (8/8)` plus `e2e`.

### How it works

The reusable workflow input `python_slices` becomes `[1, 2, 3, 4, 5, 6, 7, 8]`; full Python selection and `e2e: true` remain. `REQUIRED_JOBS` is generated for the same eight names. The fake GitHub jobs response is updated to match. This aligns partitioning with the existing per-job timeout instead of increasing that timeout.

### Code

- `.github/workflows/upstream-sync.yml`: reusable full-suite invocation.
- `scripts/ci/upstream_sync.py`: required job-name tuple.
- `tests/hermes_cli/upstream_gate_fake.py`: eight-slice API fixture.

### Tests

Source tests inspected, not executed in this mirror pass:

- `tests/hermes_cli/upstream_gate_fake.py`: fixture now emits all eight Python jobs and `e2e`; it is support code, not a test by itself.
- `tests/scripts/ci/test_upstream_sync.py::test_red_sync_is_refused_and_green_sync_lands`: stages a real local Git candidate, runs fixture-checkout pytest, and checks refusal versus landing the tested head through a merge.
- `tests/scripts/ci/test_upstream_sync.py::test_incomplete_job_evidence_cannot_land`: checks refusal when required evidence is missing, skipped, hosted rather than self-hosted, or lacks a runner name. Its fake enumerates the current `REQUIRED_JOBS`.

No new independent assertion pins the workflow's literal slice list in this diff; it was inspected directly.

### Applying

Use the whole-series procedure above. Keep workflow matrix values and gate job names synchronized with the reusable tests workflow.

### Gotchas

Changing only the matrix or only `REQUIRED_JOBS` can strand the gate. The tests simulate GitHub evidence; they do not establish actual runner capacity or full-suite duration.

### Plugin?

No. This is repository CI and its exact-head publication gate.

## 1308 — Ephemeral runner image source and isolation probes

Source: `50731967b295dfda6a370165648cba2acad51bf4`.

### Behavior

Adds Ubuntu 24.04 runner-image source, boot hooks, GitHub App bootstrap helpers, and an opt-in probe workflow. The intended execution boundary is one disposable VM per job, with privileged job tools available only after instance metadata access has been disabled and checked. The upgrade workflow retains its dedicated `ci-bwrap` label and now documents on-demand VM placement.

### How it works

Packer provisions compiler/Python/Node tooling, Rust, GitHub CLI, Docker/buildx, AWS CLI, GUI build headers, `libatomic1`, and the runner agent. Bake-time checks exercise plain-PATH Rust tools, unprivileged bubblewrap, apt package availability and home-directory ownership. The image removes the management agent that could retain instance-role credentials and lifts the Ubuntu user-namespace restriction needed by sandbox tests.

Before the runner starts, `ghr-imds-hook` reads instance identity, tags, role credentials and the one-shot JIT configuration, deletes that configuration's parameter, disables the instance metadata endpoint, and requires both control-plane readback and a failing token PUT. Only then does it write the JIT configuration with restrictive permissions. `ghr-start` consumes and removes that file, runs the agent as the runner user, and powers off when the agent finishes; hook failure also powers off without starting the agent.

The manual `tfgar-probe` workflow checks metadata-token denial, absence of usable AWS credentials, TCP denial for selected private-range endpoints, a public-egress control, and toolchain/root availability. Bootstrap helpers convert a GitHub App manifest and store credentials in parameter storage. A separate script prepares a probe branch targeting a dedicated runner label.

### Code

- `.github/actionlint.yaml`, `.github/workflows/tests.yml`, `.github/workflows/tfgar-probe.yml`.
- `hosts/hermes-ci-runners/packer/runner.pkr.hcl`, `packer/provision.sh`, `packer/files/ghr-imds-hook`, `packer/files/ghr-start`.
- `hosts/hermes-ci-runners/bootstrap/github-app-manifest.json`, `bootstrap/serve-manifest.py`, `bootstrap/store-github-app.sh`.
- `hosts/hermes-ci-runners/probe/make-probe-branch.sh` and `hosts/hermes-ci-runners/README.md`.

The source infrastructure location and account-specific rollout details are deliberately not reproduced here.

### Tests

Source tests inspected, not executed in this mirror pass:

- `hosts/hermes-ci-runners/tests/test_imds_hook.sh`: stubs `curl`, `aws`, `sudo` and poweroff. Checks successful JIT publication; failure with no JIT file when control-plane readback remains enabled, metadata remains reachable, or the modify call fails; the token PUT's `curl -f` form; no runner start plus poweroff on hook failure; and runner start, JIT removal and poweroff on success.
- `.github/workflows/tfgar-probe.yml`: executable manual integration probes, not a local unit suite. These check the deployed environment only when dispatched; no such dispatch occurred here.
- `hosts/hermes-ci-runners/packer/provision.sh`: inline bake assertions described above; no bake was executed here.

### Applying

Use the whole-series procedure above. Applying source does not deploy infrastructure. An adopter must supply their own cloud/IAM/network configuration, GitHub App installation, image rollout and shutdown-to-termination policy, then run the probe on the dedicated label before routing real jobs.

### Gotchas

The infrastructure stack is external to this source change. A VM grants jobs passwordless sudo and Docker: VM lifecycle and networking, not an unprivileged job user, provide isolation. Hook credentials are passed in the environment of its AWS CLI subprocesses; they are not exported to the later runner process. Do not interpret the shell comments as proof that no child process ever receives credentials.

Metadata denial treats a rejected token PUT, including HTTP 403 under `curl -f`, as unavailable; a tokenless GET would not prove this. The network probe samples particular endpoints and ports, not every private address or protocol. Its source targets must be adapted to the adopter's network. Removing credential-caching agents also removes their debugging path. The probe-branch helper switches branches and depends on a source branch; use a disposable worktree and review the resulting matrix, never a live pinned checkout.

### Plugin?

No. These are image, workflow and deployment-support assets, not an agent plugin.

## 1309 — Make the symlink CLI test install-shape independent

Source: `bbae9719e033c4d150fb830776fe37a0e0bd3dcc`.

### Behavior

The symlinked workspace-check CLI regression test no longer requires the Desktop workspace to be installed. It still rejects a symlink launch that silently lists nothing.

### How it works

The test runs `--list` through a temporary symlink and directly through the real script. It requires a `tests-js :: check` entry with a memory estimate and exact equality between both outputs. This respects `npm query .workspace`, which reports the installed subset rather than every manifest in the repository.

### Code

- `tests-js/run-workspace-checks.test.mjs`: symlinked-entry test only; no scheduler implementation changes.

### Tests

Source tests inspected, not executed in this mirror pass:

- `tests-js/run-workspace-checks.test.mjs`, test `the CLI runs when started through a symlinked path, so it never exits green having run nothing`: asserts a nonempty expected workspace entry, direct/symlink parity and fixture cleanup in `finally`.

### Applying

Use the whole-series procedure above. Retain the real workspace-check script and the workspace manifest supplying the `tests-js` check.

### Gotchas

This exercises discovery/listing, not execution of every listed check. It does not force a full Desktop install into the minimal TUI job.

### Plugin?

No. Test-only CI tooling correction.

## 1317 — Preserve ownership of detached E2E gateways

Source: `6f476918c35312587e318060a4f8f5d924be8905`.

### Behavior

Core Desktop E2E sandbox environments preserve `HERMES_E2E_RUN_TMP`, allowing existing run/host reapers to identify a detached fixture gateway after its runner exits or is killed.

### How it works

`coreAppEnv` explicitly copies the run-root marker from the parent when present, despite runtime-environment sanitization. A new Node/TypeScript helper starts a real Python `gateway ensure` inside a sandbox and reports its detached gateway PID, run root and home. Tests either kill that owning runner or ask it to exit, then inspect process identity and sandbox removal. The reaping implementation itself is not added by this patch.

### Code

- `apps/desktop/e2e/core/harness.ts`: `coreAppEnv` ownership-marker preservation.
- `apps/desktop/e2e/core/reaper-runner.ts`: real runner subprocess fixture.
- `apps/desktop/e2e/run-tmp-reaper.gateway.unit.test.ts`: Linux lifecycle regressions.

### Tests

Source tests inspected, not executed in this mirror pass:

- `apps/desktop/e2e/run-tmp-reaper.gateway.unit.test.ts`, test `reaps gateway ensure after a SIGKILLed runner when the next runner starts, but not a concurrent runner`: observes a real detached gateway surviving owner SIGKILL, then its removal and root deletion on a new runner's startup; a concurrent reaper must leave the current runner's gateway/root intact.
- The same file, test `stops an unfinished fixture gateway on normal runner exit`: asserts gateway termination and root removal after normal runner exit without sandbox teardown.

### Applying

Use the whole-series procedure above. Preserve the earlier `run-tmp` reaper, process-census and sandbox harness. Tests require Linux, `tsx`, a configured `HERMES_PYTHON` and the actual Python gateway; they do not require Electron.

### Gotchas

This is fixture lifecycle ownership, not a general runtime setting to reintroduce after environment scrubbing. Tests are Linux-only and clean up leftover gateways only after checking exact sandbox-home ownership. No macOS cleanup claim is established by these tests.

### Plugin?

No. Core E2E harness/environment behavior.

## 1320 — Gate frontend changes on the minimal TUI install

Source: `5974f72bb3e63b27612e29e6d368aa23eeaa1c33`.

### Behavior

Frontend CI gains a blocking `Prebuilt TUI install-shape checks` job using the same minimal workspace install and check/build commands as the existing Termux prebuilt-TUI job. Changes to the shared ESLint configuration and root `.npmrc` now select the frontend lane in addition to Python.

### How it works

The reusable JS workflow checks out the requested commit without persisted credentials, runs package-manager setup with the required toolchain and no node cache, installs only `ui-tui` and `tests-js` plus workspace root/development dependencies, then runs checks for both and builds `ui-tui`. A clean-checkout step follows. The job has a 15-minute timeout and no local conditional or `continue-on-error` escape.

### Code

- `.github/workflows/js-tests.yml`: `tui-install-shape` job.
- `scripts/ci/classify_changes.py`: root shared inputs in `_FRONTEND_FILES`.
- `tests/ci/test_classify_changes.py`, `tests/ci/test_tui_install_shape.py`.

### Tests

Source tests inspected, not executed in this mirror pass:

- `tests/ci/test_tui_install_shape.py::test_main_ci_job_matches_the_termux_prebuilt_tui_job`: compares setup inputs and all run commands, requires the minimal install without Desktop, requires test/build commands, and rejects a nonblocking job.
- `tests/ci/test_tui_install_shape.py::test_job_gates_the_merge_through_js_tests`: asserts the main CI invokes the JS reusable workflow for frontend changes, the aggregate gate needs it, and the shape job has no extra `if`.
- `tests/ci/test_tui_install_shape.py::test_every_input_of_the_shape_selects_the_frontend_lane`: parameterized assertions for workspace sources, runner script, root package files, TUI build script, shared ESLint config and `.npmrc`.
- `tests/ci/test_classify_changes.py`: changed table cases assert Python-plus-frontend classification for the shared ESLint config and `.npmrc`.

### Applying

Use the whole-series procedure above. Keep the job's setup/run shape aligned with `.github/workflows/termux-verify.yml`; parity tests deliberately detect drift.

### Gotchas

A successful full-workspace installation is not evidence that this smaller installation works. The workflow-parity tests inspect YAML and can skip when the YAML helper dependency is unavailable; no minimal install or bundle was executed for this doc.

### Plugin?

No. Merge-gating workflows and change classification.

## 1321 — Diagnose workspace stalls and isolate temporary fixtures

Source: `35b214a8ee1700ac94fe2793d184491499eb5bb4`.

### Behavior

Workspace checks collect temporary-directory write/fsync latency, event-loop lag and per-run cgroup pressure/counter deltas. CI also samples checkout-disk latency. The main JS check job prefers executable memory-backed temporary storage and reserves its full tmpfs capacity from the scheduler's memory budget.

### How it works

A non-overlapping probe writes and fsyncs a 4 KiB file roughly once per second, then removes it. Samples retain labels for running check units. Summaries report stalls at least 1 second, severe stalls at least 5 seconds, worst slow samples, timer lag, I/O/memory/CPU PSI deltas in milliseconds and `memory.events` high/max deltas. Missing pressure data stays `null`; failed probe writes are skipped. A second probe measures the checkout directory in CI, separately from fixture storage.

The workflow attempts a 1 GiB executable tmpfs, falls back to an executable shared-memory mount of at least 512 MiB, then to a short disk-backed temporary directory. It exports that directory as `TMPDIR` for checks. An EXIT trap reports usage, unmounts a mount it created (with lazy fallback), and removes the temporary directory. Linux filesystem-type detection subtracts the whole tmpfs capacity in addition to the normal runner reserve; non-tmpfs storage adds no such reserve.

### Code

- `.github/scripts/run-workspace-checks.mjs`: tracked running units, both probes, mount/cgroup discovery and summaries.
- `.github/scripts/workspace-check-stall-probe.mjs`: counter parsing, sampling and summary functions.
- `.github/scripts/workspace-check-temp-memory.mjs`: Linux tmpfs capacity detection.
- `.github/scripts/workspace-check-admission.mjs`: optional temporary-memory subtraction.
- `.github/workflows/js-tests.yml`: temporary-storage selection and cleanup shell.

### Tests

Source tests inspected, not executed in this mirror pass:

- `tests-js/workspace-check-stall-probe.test.mjs`: asserts PSI/event parsing, per-run deltas and units, missing-data nulls, stall thresholds/worst ordering, injected slow-write labels, stop behavior, failed-write omission, and real fsync sampling with no remaining probe files.
- `tests-js/workspace-check-temp-memory.test.mjs`: on Linux asserts a 1 GiB tmpfs reservation reduces the modeled 8 GiB budget to 6144 MiB; the disk-backed case asserts no extra reservation and a 7168 MiB budget.
- `tests-js/workspace-check-temp-shell.test.mjs`: executes shell extracted from the real workflow with mount failure and unusable shared-memory fallback forced. Asserts a nested Unix socket and executable helper work, temporary storage is removed, and an injected exit status of 7 survives cleanup.

Successful tmpfs mounting, executable shared-memory fallback and real cgroup PSI collection are not exercised end-to-end by these new tests.

### Applying

Use the whole-series procedure above. Keep probe imports, the admission argument and workflow storage selection together: tmpfs pages consume the same cgroup budget as checks.

### Gotchas

Telemetry is diagnostic, not proof of a particular root cause or a repair of the backing disk. Severe stalls emit warnings; they are not independently converted into check failures. Missing samples do not mean a fast disk. The shared-memory fallback reserves the whole mount capacity, not merely its current use; admission clamps the remaining budget to at least 1 MiB. Linux-specific mount/cgroup behavior differs from other platforms. `stop()` waits for any in-flight probe operation.

### Plugin?

No. Workspace CI runner, scheduler and workflow instrumentation.

## 1322 — Tolerate exiting peers in MCP test liveness scans

Source: `abf5f8578aa516ecbcba991f6d8e36e6274c97f2`.

### Behavior

The editor-MCP integration tests' process scanner tolerates a peer exiting or being reaped between child enumeration and reading argv, without losing a separate live peer. This is **test-helper hardening**, not a production MCP runtime change.

### How it works

`_peers_running` becomes a loop around each child's `cmdline()` read and catches `psutil.NoSuchProcess` per child (including its `ZombieProcess` subclass). The regression test starts two real peer processes, waits for their argv publication, then kills one exactly when its command line is read. Parameterization leaves that child a zombie or reaps it before the read.

### Code

- `tests/acp_adapter/test_gateway_editor_mcp.py`: `_peers_running` helper.
- `tests/acp_adapter/test_editor_mcp_peer_liveness.py`: new process-race test.

### Tests

Source tests inspected, not executed in this mirror pass:

- `tests/acp_adapter/test_editor_mcp_peer_liveness.py::test_peer_exiting_during_scan_does_not_hide_a_live_peer`: Linux-only `zombie` and `reaped` variants assert both peers are initially discoverable, the exiting peer is read exactly once and omitted afterward, and the live peer remains present.

### Applying

Use the whole-series procedure above. Retain the shared gateway-editor test module imported by the new test and the process-wait helper.

### Gotchas

The catch is intentionally limited to disappearing processes while reading each child's argv. It does not newly handle arbitrary access errors or failure of the initial parent/child enumeration. The targeted race test is Linux-only.

### Plugin?

No. Test-only process census resilience.
