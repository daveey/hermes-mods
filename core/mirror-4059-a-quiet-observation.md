# Attended claim-quiet observation and work custody

Mirror ordinals: 3741, 3742, 3743, 3744, 3745, 3746, 3747, 3748, 3749, 3777.

## Behavior

- `hermes kanban quiet-hold --max-s N --reason TEXT -- OBSERVER_COMMAND...` temporarily pauses automatic native-local claims while an attended observer runs. It does not stop existing work, gate cron, or disable manual work.
- Quiet measurements are valid only with authenticated lease custody, a stable gateway identity, complete bootstrap/code/membership coverage and continuously clean work/predicate evidence. Unknown coverage refuses a proof rather than pretending the gateway is idle.
- Gateway ingress, agents, helper calls, tool dispatch, executors and child-process custody gain local work accounting. Work between polls invalidates the captured baseline through epoch changes.
- The observer reports valid CPU acceptance/failure separately from INVALID evidence. The gateway's own polling CPU remains in the numerator; no polling overhead is subtracted.
- Later patches correct bootstrap/thread compatibility, profile-membership seeding, child adoption and dependency-ledger limits. A positive allowlist suppresses stuck warnings only for truly quiet-only deferrals.

## How it works

`claim_quiet` uses a bounded 1–480-second lease, a cooldown record and an exclusively held file lock. Lease records bind lock inode/device, canonical root, process incarnation/birth and boot-aware suspend-inclusive timing; strict JSON parsing, no-follow directory/record access and atomic fsynced writes reject malformed or replaced custody. Owner close removes only its own lease, and fork handling prevents a child inheriting authority. The CLI supervises an observer process group, cleans up leftovers and releases custody on completion, cancellation or expiry.

`local_work.Registry` reserves before scheduling and releases after confirmed completion. It tracks epoch, per-kind/per-home work, membership transitions and sticky unknowns without a clear-unknown escape hatch. Tracked tasks/threads/executors retain reservations across cancellation until the underlying work actually ends. Child reservations survive until verified exit; an unprovable birth/exit latches unknown instead of disappearing from the proof. Profile ownership seeds canonical homes at the actual runtime-ownership boundary, including borrowed ownership, and pending membership transitions refuse quiet.

Early launch/bootstrap installs the registry and executed-code audit ledger. Reviewed managed-stub heads/tails establish a supported route; unsupported launch paths, source skew, prehook imports and unrooted execution remain unknown. Installer RECORD receipts attest dependency origin/integrity but explicitly do not audit dependency coverage. Reviewed generated-code producers are bounded to exact source/call-site identities; generated mismatch and origin failure remain unknown. The loaded-code ledger limit is raised to 2^18 with an explicit above-old-cap regression.

Peer-authenticated private control verbs start/snapshot/end an observer episode and validate its token, deadline, lease and exact gateway identity. Measurement combines registry evidence with real ingress/lease/runtime/update predicates and snapshot sequencing. `observe_gateway_quiet.py` settles only transient conditions, then requires a contiguous stable capture with advancing clocks, readable non-regressing CPU, no excessive gap, no sleep/wall jump and unchanged identity/membership/epoch.

Dispatch checks the lease before automatic claim and again at the admission boundary, after stronger capsule refusals. Quiet deferrals carry task/episode/deadline metadata without claim, refusal event or spawn. Watcher/FIFO/unloaded entry paths defer only their automatic admission. The final stuck-warning guard classifies every DispatchResult field: any populated field outside the harmless quiet/status/timing allowlist preserves ordinary diagnostics. No actual CPU observation, gateway launch or live quiet hold was performed here.

## Code

- `agent/auxiliary_call_scope.py`
- `agent/auxiliary_client.py`
- `agent/auxiliary_probe_stub.py`
- `agent/claim_quiet.py`
- `agent/claim_quiet_records.py`
- `agent/local_work_agent.py`
- `agent/memory_manager.py`
- `agent/memory_provider.py`
- `agent/review_idle_queue.py`
- `agent/title_generator.py`
- `agent/turn_facade.py`
- `cron/scheduler.py`
- `default/cache/plugin_hooks.json`
- `gateway/control_dispatch.py`
- `gateway/kanban_watchers.py`
- `gateway/kanban_watchers_dispatcher.py`
- `gateway/platforms/api_server_runs.py`
- `gateway/platforms/base.py`
- `gateway/platforms/base_channel_skills.py`
- `gateway/quiet_control.py`
- `gateway/run.py`
- `gateway/run_adapters.py`
- `gateway/run_bootstrap.py`
- `gateway/run_turn_hygiene.py`
- `gateway/runtime_ownership.py`
- `gateway/session_authority.py`
- `gateway/session_controls.py`
- `gateway/session_ingress.py`
- `gateway/session_ingress_burst.py`
- `gateway/session_managed_worker.py`
- `gateway/session_notifier_drain.py`
- `gateway/session_runtime_workers.py`
- `gateway/session_worker_spawn.py`
- `hermes_bootstrap.py`
- `hermes_cli/_launch_template.py`
- `hermes_cli/_launchers.py`
- `hermes_cli/code_attestation.py`
- `hermes_cli/kanban_capacity_evidence.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_db_dispatch_admission.py`
- `hermes_cli/kanban_db_dispatch_custody.py`
- `hermes_cli/kanban_db_dispatch_pid.py`
- `hermes_cli/kanban_db_dispatch_quiet.py`
- `hermes_cli/kanban_db_dispatch_stuck.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_quiet.py`
- `hermes_cli/local_work.py`
- `hermes_cli/local_work_threads.py`
- `hermes_cli/plugin_host.py`
- `hermes_cli/plugins_dispatch.py`
- `hermes_cli/web_routers/audio.py`
- `hermes_cli/web_server_chat.py`
- `hermes_platform/quiet_clock.py`
- `model_tools.py`
- `owned/cache/plugin_hooks.json`
- `p/cache/plugin_hooks.json`
- `plugins/platforms/discord/adapter.py`
- `plugins/platforms/slack/adapter.py`
- `pm/environments.py`
- `pyproject.toml`
- `run_agent.py`
- `scripts/observe_gateway_quiet.py`
- `target/cache/plugin_hooks.json`
- `tools/code_execution_rpc.py`
- `tools/code_execution_tool.py`
- `tools/code_kernel_remote.py`
- `tools/code_kernel_scope.py`
- `tools/delegate_tool_child_run.py`
- `tools/delegate_tool_dispatch.py`
- `tools/mcp_tool_loop.py`
- `tui_gateway/contracts/activity.py`
- `tui_gateway/delegation_unloaded.py`
- `tui_gateway/kanban_unloaded.py`
- `tui_gateway/producer_admission.py`
- `tui_gateway/rpc_dispatch.py`
- `tui_gateway/startup_resume.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions present in the staged diffs, not fresh execution results.

- `tests/agent/test_claim_quiet.py` — Strict record/clock/lease validation, active-versus-unheld custody, cooldown, expiry, root/lock changes and acquisition/release/fork safety are asserted; the follow-up uses SystemClock so patched boot identity reaches the actual clock seam.
- `tests/agent/test_claim_quiet_races.py` — Concurrent acquisition, directory/lock replacement and fork/cleanup races retain exclusive custody or refuse measurement rather than leaving a falsely valid lease.
- `tests/agent/test_local_work_agent_seams.py` — Agent turns, memory/title/review/auxiliary and submission seams acquire/release the intended work scope and retain in-flight work; no installed registry remains a no-op.
- `tests/agent/test_local_work_provider.py` — Provider scope bindings/counts survive the relevant call and clean up; absence of a registry does not alter provider behavior.
- `tests/cron/test_quiet_cron_preamble.py` — Cron is counted before its preamble but still runs under a quiet lease, rather than using quiet as a cron admission gate.
- `tests/gateway/test_api_server_active_work_drain.py` — An unstarted API request drops its active-work count without leaking a drain hold.
- `tests/gateway/test_quiet_control.py` — Episode schema/token/peer/identity/deadline checks, bootstrap coverage, pending/live work, epoch changes, update collisions and end invalidation refuse false clean snapshots; thread-backed test fakes retain their count until real completion.
- `tests/gateway/test_quiet_gates_fifo.py` — Automatic FIFO admission defers with quiet, then resumes after the lease; work already active and stronger refusal paths retain their semantics.
- `tests/gateway/test_quiet_gates_watcher.py` — Watcher admission recognizes quiet-only results without a stuck alarm and preserves other suppression reasons.
- `tests/gateway/test_quiet_ingress_api_authority.py` — API/authority work is reserved across admission and the selected claim attempt, with cleanup on cancelled/unstarted paths.
- `tests/gateway/test_quiet_ingress_executors.py` — Ingress/executor reservations start before enqueue and remain active through real background completion despite cancellation.
- `tests/gateway/test_quiet_membership_seeding.py` — Runtime-ownership acquisition and borrowed homes seed canonical registry membership; transition/failure conditions stay pending or unknown rather than clean.
- `tests/gateway/test_slack_channel_skills.py` — Adapter skill-resolution fixtures remain compatible with the shared resolver import/re-export, preserving existing skill selection expectations.
- `tests/hermes_cli/test_code_attestation.py` — Executed source/generated code is rooted and hashed, unsupported or skewed code latches unknown, and code limits/fork state fail closed.
- `tests/hermes_cli/test_code_attestation_deps.py` — Installer-shaped RECORD fixtures establish dependency receipts separate from audited units; tampering/unreceipted execution is unknown. The follow-up loads more than 16,384 dependency code objects without unknowns and stays below the revised bound.
- `tests/hermes_cli/test_code_attestation_producers.py` — Exact typing/ForwardRef producers generate dependency-origin receipts; hash mismatch, unlisted producer, missing callsite, swapped code and unattested caller latch specific unknowns. Repeated evaluation and module reexecution stay bounded; hook compiles run under the recursion guard.
- `tests/hermes_cli/test_code_attestation_route.py` — Real subprocess managed-stub routes establish reviewed bootstrap provenance; changed head/tail/root, console/relaunch/module/post-swap routes, prehook imports and dirty seed source refuse it. The historical stub is a data fixture, not pytest configuration.
- `tests/hermes_cli/test_kanban_quiet.py` — CLI validation, successful observer, nonzero/cancel/timeout paths, process-group cleanup and release failures preserve exit/custody behavior.
- `tests/hermes_cli/test_local_work.py` — Reserve/finish, epochs/per-kind counts, unmatched finishes, unbound homes, fork inheritance and immutable sticky unknowns prevent an incomplete registry appearing clean.
- `tests/hermes_cli/test_local_work_membership.py` — Canonical membership joins/retires and outstanding work move generations and coverage; unknown or retired-home reentry stays visible.
- `tests/hermes_cli/test_local_work_threads.py` — Task/thread/executor success, failed start, cancellation and uncertain enqueue retain or release custody correctly. Replaced Thread classes latch coverage unknown; rejected executor submissions release only when rejection is provable.
- `tests/hermes_cli/test_quiet_gates_dispatch.py` — Quiet deferral produces no claim/spawn/event, stronger gates win, and expiry resumes admission. Every DispatchResult field belongs to one classification; quiet-only/empty-board companions suppress warnings, while ownership, error, cap, future fields and mixed refusals do not.
- `tests/hermes_cli/test_quiet_ingress_audio.py` — Audio provider/executor and streaming paths reserve work across their actual execution, retain abandoned in-flight work and release finished work.
- `tests/hermes_cli/test_quiet_kanban_spawn_custody.py` — Spawn reservations attach even if child birth probing raises; verified reap releases, unverified disappearance retains the token and latches unknown.
- `tests/hermes_cli/test_quiet_pty_custody.py` — PTY spawn failure releases before start, live child custody survives close until death is verified, and uncertain exit remains counted/unknown.
- `tests/hermes_cli/test_source_launcher_publication.py` — Launcher assertions match the new bootstrap invocation so quiet coverage installation does not silently bypass publication expectations.
- `tests/hermes_cli/test_web_server_pty_import.py` — The wrapped PTY class remains import-compatible with the web-server integration.
- `tests/scripts/test_observe_gateway_quiet.py` — Deterministic samples establish CPU threshold outcomes versus invalid evidence; identity/epoch/membership changes, bad sequence, sleep/gaps/clocks/counters/transport invalidate the capture, while output retains measured CPU and polling-overhead semantics.
- `tests/tools/test_local_work_tools.py` — Tool, code-kernel/RPC, delegation and MCP execution scopes retain work until their underlying task/thread finishes, including cancellation/no-registry controls.
- `tests/tui_gateway/test_quiet_sweeper_scope.py` — Unloaded/startup producer sweeps scope their automatic admission so quiet defers it without gating unrelated work.
- `tests/tui_gateway/test_rpc_dispatch_activity.py` — Only the explicit passive catalog avoids work reservations; unknown and work-bearing RPC methods are counted, with method signature/dispatch compatibility preserved.

## Applying

Use the full ordered mirror series and the **Applying** recipe in [README.md](README.md), including every historical bridge/replay boundary and the mandatory snapshot-bound hostname escape repair plus its repaired-tree check. These ordinals are not independent cherry-picks. This document does not establish replay success, deployment, or live activation; the parent mirror verification supplies replay evidence separately.

## Gotchas

Quiet is a measurement/admission protocol, not a CPU throttle or a guarantee that a running gateway is idle. Only a supported early-attested launch may claim coverage; late installation cannot repair missed startup work. Dependency receipts are trusted installer evidence, never audited coverage. Passive RPCs still consume measured CPU. Invalid evidence cannot be downgraded to a low-CPU success or reconstructed by fallback process scans. The observation and child-process tests document specific seams; this pass did not run any real gateway/REST/UI end-to-end observation.

## Plugin?

No. A plugin may contribute tracked work or use the observer, but early bootstrap attestation, admission fencing, profile membership and child custody must be established by core before plugins or work run.
