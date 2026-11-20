# Upstream reconcile, compat bootstrap seam, native allocator tests and uv-gated CI toolchain

Mirror snapshot 5011; ordinals 4353, 4354, 4439, 4440, 4452.

Entries:

- **4353**: fix(upsync): reconcile fork behavior with upstream b46e214954
- **4354**: Reconcile upstream compat bootstrap and process kill test seam
- **4439**: Test native bootstrap allocator re-exec and require Darwin CI
- **4440**: Add unittest-discovery guard to native bootstrap allocator tests
- **4452**: fix(ci): gate Python command environment on uv closure

## Behavior

- **Upstream reconcile (4353)**: a fork-vs-upstream merge reconciliation. A draining submit refusal in the scheduler authority now awaits the fork's async journal save; claim-loop admission and checkpoint reads move off the owner event loop; the per-task shell-state chain from upstream is preferred, with the fork overlay only as a fallback; notification and auto-continue claims settle through a single release helper. Several upstream tests are adapted to fork surfaces (refusal frame, pinned-unit notice, full-tree detect job, legacy fixtures) while keeping file-size caps.
- **Compat bootstrap (4354)**: the bootstrap module keeps compatibility modules observable as executed code after an interrupted-update repair and installation audit, rather than silently adding them to the trusted seed. A process-completion test seam for signal-reader exit is aligned with upstream.
- **Native allocator tests (4439, 4440)**: real bootstrap and real `exec` boundaries are exercised in a driver subprocess (only dependency preparation is stubbed) to prove the allocator environment policy is set for server launches and that the re-exec happens exactly once. The change classifier now routes allocator-launch changes to the macOS native-memory workflow, which is required on Darwin CI. A unittest-discovery guard stops the allocator tests from being picked up in a way that bypasses the subprocess driver.
- **CI toolchain (4452)**: the uv-managed Python command environment is built only when uv is among the selected tools. Node plus ripgrep pulls Python transitively but never installs uv, so the old unconditional build failed there; `python` and `all` selections are unchanged.

## How it works

- 4353 touches the credential pool, cron scheduler authority, gateway run-class routing / turn preparation / session authority / managed-worker session, kanban dispatch, the CLI entry, the environment base and several TUI gateway notification/auto-continue modules; mostly small edits that re-point to existing seams.
- 4439 adds a workflow file `native-memory-macos.yml`, extends `scripts/ci/classify_changes.py` and adds a gate test and a workflow-shape test. 4440 only guards test discovery.
- 4452 gates the uv environment step in `scripts/ci/setup_toolchain.py` on the resolved tool closure.

## Code

Shipped non-test paths (union over these entries):

- `.github/workflows/native-memory-macos.yml`
- `agent/credential_pool.py`
- `cron/scheduler_authority.py`
- `gateway/run_class_routing.py`
- `gateway/run_turn_prepare.py`
- `gateway/session_authority.py`
- `gateway/session_managed_worker.py`
- `hermes_bootstrap.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/main.py`
- `scripts/ci/classify_changes.py`
- `scripts/ci/setup_toolchain.py`
- `tools/environments/base.py`
- `tui_gateway/methods_prompt.py`
- `tui_gateway/session_auto_continue.py`
- `tui_gateway/session_notifications.py`
- `tui_gateway/session_notifications_surface.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here):

- `tests/agent/test_redact.py`
- `tests/agent/test_redact_tavily.py`
- `tests/ci/test_classify_changes.py`
- `tests/ci/test_native_memory_gate.py`
- `tests/ci/test_native_memory_workflow.py`
- `tests/gateway/test_process_completion_custody.py`
- `tests/hermes_cli/test_update_pause_pinned_runtime.py`
- `tests/hermes_cli/test_ux_messages_startup.py`
- `tests/scripts/test_setup_toolchain_install.py`
- `tests/test_bootstrap_malloc.py`
- `tests/tui_gateway/test_idle_lease_refusal_edges.py`
- `tests/tui_gateway/test_idle_lease_release_races.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered patch list (every segment, in order) with `git am -3 --empty=keep`, followed by any repair overlay that README names for the segment. They are not independent patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- 4353 is a reconcile commit: it intentionally mixes small behavior fixes with test adaptations, so it should be reviewed against the upstream merge it reconciles.
- The native allocator tests spawn real subprocesses with a fresh HOME; they need a Darwin runner for the allocator-specific paths and are time-bounded at 30 s per driver run.
- 4452 verified cold installs in isolated homes with account seeding disabled; no claim is made about previously installed toolchains.
