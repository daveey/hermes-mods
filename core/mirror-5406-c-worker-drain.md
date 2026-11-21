# Activation-gated worker drain leases and conservative census

Mirror entries: 5269, 5270, 5271, 5272, 5273, 5274, 5275, 5276, 5277, 5278, 5279, 5280. Related implementation and regression commits are grouped here; later fixes in the group supersede their intermediate behavior.

## Behavior

Activated POSIX worker nodes gate new work behind a durable instance/boot/epoch/attempt-bound drain latch. Absent activation, hooks are no-ops; malformed or mismatched active state refuses rather than reading as open. Live inherited leases let admitted descendants finish while draining, but sealed nodes refuse.

A zero persistent-job census requires every manifest adapter to prove idle and every local result to be acknowledged. Unreadable inventories and pending outbox/sync rounds never imply zero. **Incomplete:** this source range has no production writer for the trusted live process baseline; consequently production host-process observations cannot establish ZERO and this feature does not enable scale-in.

## How it works

State lives at the shared activated account root under `core-update`, independent of profile and CPU-policy setting. Atomic fsynced latch writes, account flock, exact request hashing, finite mutation deadline and monotonic lock-wait charging fence establish/cancel/seal transitions. Seal itself checks busy leases. State/activation trust checks reject symlinks, hardlinks, foreign owners and unsafe writable modes. Installer-only adoption tightens trusted pre-existing directories; it does not accept foreign/symlinked state. Root-run admission and census use the activated account's root.

Work leases are close-only flocked files; their existence alone is not busy. Detached children inherit descriptors/environment, PTY setup adjusts inheritable flags in the child, and reservations/turn callbacks retain context-scoped authority until close. Parent authority is revalidated through descriptor identity, live kernel flock and boot binding. Core routes selected terminal/process-registry, worker, browser, router, compute host, direct TUI exec, cron, shell-hook, CUA and MCP launch seams through admission/lifetime handling.

The final manifest comprises `host_processes`, `work_leases`, `local_results`: positive observations yield a lower bound; every adapter must be ZERO to emit zero, otherwise UNKNOWN. A root-owned nonwritable baseline would identify exact UID/cgroup/executable triples; a working descendant prevents blanket infrastructure exemption. The results adapter reads every account board read-only and also probes its in-flight sync lock. Source inventory/alias-aware scan marks fenced sites and explicitly pending/incomplete seams rather than treating inventory inclusion as enforcement.

## Code

Paths below are relative to the source Hermes checkout, not files supplied by this documentation:

- `agent/shell_hooks.py`
- `cron/scheduler.py`
- `cron/scheduler_tick.py`
- `fixture/cache/plugin_hooks.json`
- `hermes_cli/admission_fence.py`
- `hermes_cli/backend_retirement.py`
- `hermes_cli/kanban_admission.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/local_runtime/processes.py`
- `hermes_cli/machine_persistent_jobs.py`
- `hermes_cli/process_identity.py`
- `hermes_cli/pty_bridge.py`
- `hermes_cli/worker_drain_census.py`
- `hermes_cli/worker_drain_fence.py`
- `hermes_cli/worker_drain_results_gate.py`
- `hermes_cli/worker_drain_spawn_inventory.json`
- `tools/browser_launcher.py`
- `tools/computer_use/cua_backend_session.py`
- `tools/environments/base_output.py`
- `tools/environments/local.py`
- `tools/mcp_tool_transport.py`
- `tools/process_registry.py`
- `tools/process_registry_ownership.py`
- `tools/process_registry_survivable.py`
- `tui_gateway/host_supervisor.py`
- `tui_gateway/methods_tools.py`
- `tui_gateway/turn_admission.py`

## Tests

**Source test coverage only — not executed by this mirror documentation task.**

Source tests cover latch generations/replay/expiry, activation/no-op behavior, cross-process lease lifetime, inherited descendant authority, late-spawn refusal, claim transactions, terminal/PTy and detached child lifetimes, spawn failure cleanup, reservation/thread/cron/backend lifetime, seal checking and forged parent rejection. Census tests cover unscoped work, kernel identity, unreadable entries, exact baseline trust/descendants, exact adapters, outbox and active-sync gates. Hardening tests cover directory adoption, symlink/hardlink/owner/mode rejection, root-account consistency and deadline lock wait. Composed fixtures feed idle/positive/missing-proof/stale-sample observations through the producer-to-scaler boundary using an injected trusted baseline. The source scan tests pin aliases and honest pending/incomplete entries.

Relevant source suites/fixtures:

- `tests/hermes_cli/test_worker_drain_census.py`
- `tests/hermes_cli/test_worker_drain_census_manifest.py`
- `tests/hermes_cli/test_worker_drain_composed.py`
- `tests/hermes_cli/test_worker_drain_fence.py`
- `tests/hermes_cli/test_worker_drain_hardening.py`
- `tests/hermes_cli/test_worker_drain_results_gate.py`
- `tests/hermes_cli/test_worker_drain_source_scan.py`
- `tests/tools/test_worker_drain_launch_lifetime.py`
- `tests/tools/test_worker_drain_reservations.py`
- `tests/tools/test_worker_drain_spawn.py`

All runtime entry paths not explicitly covered above are **untested** by the cited batch coverage; no live deployment or provider run is claimed.

## Applying

Apply only as part of the **full ordered 5406-series** using the snapshot's [README Applying recipe](README.md), including its pinned replay bridges and **mandatory post-replay hostname-escape repair**. Follow the snapshot inventory and repaired-tree verification; do not cherry-pick this document's ordinals as a certified independent patch set. This document records source behavior/coverage, not independent patch certification or an executed mirror acceptance result.

## Gotchas

Do not advertise complete admission coverage: `gateway/session_worker_spawn.py` callback admission and `tools/registry.py` dispatch remain explicitly incomplete, and pending inventory files remain unfenced. Static inventory coverage is not an executed test for each tool path. Terminal/PTy/process/cron seams have targeted source tests; full browser, MCP, CUA, router and compute-host end-to-end launches are untested here. `execute_code` as an end-user entry path is untested and must not be inferred from terminal coverage. Baseline production/install capture and remote workspace/attachment-byte custody are incomplete; local outbox clearance proves neither. Actual cloud termination, fleet install and real scale-in are untested. Off POSIX activation stays disabled. Early census versions in this range were deliberately superseded by conservative and manifest-based logic.

## Why core instead of a plugin

Drain needs atomic coordination across process spawn, turn/claim admission, inherited descriptors, persistent results and heartbeat census. A plugin can request policy but cannot enforce the lifetime of every core producer or manufacture a trustworthy zero.
