# Test isolation, fixture lifecycle and CI case partitioning

**Scope:** Test-only hardening: dashboard-mount draining, joined fixture workers, virtual clocks, wait-for-ack ordering, shard splits of large cron guard suites; plus one gateway catalog-ordering fix.

**Source ordinals:** 6380, 6381, 6383, 6384, 6385, 6387, 6391, 6437, 6443, 6444, 6445, 6449, 6454, 6467, 6470, 6474, 6485, 6504, 6530, 6531, 6532, 6539, 6591, 6592

## Behavior

- Test fixtures that import the dashboard web app now take custody of the application lifecycles that import creates and drain only the handles they own before the fixture home is torn down, so a late background reader cannot write into the next test's home.
- Restart/housekeeping fixtures join retired idle workers, release both gates on startup-assertion failures, and assert the housekeeping loops actually retired.
- Timing-sensitive tests replace fixed sleeps with an observed acknowledgement (stream-open ack, viewer publication, expired-drain parameter) or caller-local virtual clocks for discovery budgets.
- Symbolic profile IDs used by gateway/ACP tests are run with an isolated cwd so late home initialisation cannot treat them as relative Hermes homes.
- The one production change in this group: gateway session control dispatch imports the catalog tables through the complete contracts package before looking up a verb, using Python package-initialization ordering rather than an asynchronous await, so background discovery does not snapshot a partially registered catalog.
- Two oversized cron lifecycle-guard suites are split into topic/shard files with the same case set (collected node IDs reconciled before/after) to stay under the per-file time cap.

## How it works

- Fixtures record the real app registry around an import and diff it afterwards; only newly-owned handles are closed.
- Where a test previously asserted after a fixed delay, the fixture now blocks on the production signal that proves the precondition.
- Shard files share fixtures via a shared helper module (session alias suite) so no case is dropped.
- `gateway/session_controls.py` waits for the registry/contract package to finish registration before it snapshots the dispatch table.

## Code

Files touched by the series (paths relative to the source tree):

- `gateway/session_controls.py`

## Tests

Source test files changed or added by the series:

- `tests/_fixtures/board_mount.py`
- `tests/_fixtures/contracts_import.py`
- `tests/acp_adapter/test_gateway_unacked_slash.py`
- `tests/cron/lifecycle_guard_session_aliases_shared.py`
- `tests/cron/test_lifecycle_guard_session_aliases.py`
- `tests/cron/test_lifecycle_guard_session_aliases_controls.py`
- `tests/cron/test_lifecycle_guard_session_aliases_nested.py`
- `tests/cron/test_lifecycle_guard_session_aliases_pipes.py`
- `tests/cron/test_lifecycle_guard_session_aliases_ssh.py`
- `tests/cron/test_lifecycle_guard_subscript_prefix.py`
- `tests/cron/test_lifecycle_guard_subscript_prefix_shard1.py`
- `tests/cron/test_lifecycle_guard_subscript_prefix_shard10.py`
- `tests/cron/test_lifecycle_guard_subscript_prefix_shard11.py`
- `tests/cron/test_lifecycle_guard_subscript_prefix_shard12.py`
- `tests/cron/test_lifecycle_guard_subscript_prefix_shard13.py`
- `tests/cron/test_lifecycle_guard_subscript_prefix_shard2.py`
- `tests/cron/test_lifecycle_guard_subscript_prefix_shard3.py`
- `tests/cron/test_lifecycle_guard_subscript_prefix_shard4.py`
- `tests/cron/test_lifecycle_guard_subscript_prefix_shard5.py`
- `tests/cron/test_lifecycle_guard_subscript_prefix_shard6.py`
- `tests/cron/test_lifecycle_guard_subscript_prefix_shard7.py`
- `tests/cron/test_lifecycle_guard_subscript_prefix_shard8.py`
- `tests/cron/test_lifecycle_guard_subscript_prefix_shard9.py`
- `tests/gateway/fixtures/queued_work_publication_daemon.py`
- `tests/gateway/test_active_session_command_authz.py`
- `tests/gateway/test_authority_lane_moves_on.py`
- `tests/gateway/test_detached_conversation_lifecycle.py`
- `tests/gateway/test_detached_conversation_results.py`
- `tests/gateway/test_internal_wake_never_interrupts.py`
- `tests/gateway/test_journey_new_session_queued_work.py`
- `tests/gateway/test_new_moves_automation_wake.py`
- `tests/gateway/test_plugin_idle_task_housekeeping.py`
- `tests/gateway/test_responses_disconnected_identity.py`
- `tests/gateway/test_session_bot_retry.py`
- `tests/gateway/test_session_deferred_reasoning.py`
- `tests/gateway/test_session_mutation_model_busy.py`
- `tests/gateway/test_stream_consumer_wecom_native.py`
- `tests/gateway/test_unviewed_completion_notes.py`
- `tests/gateway/test_unviewed_completion_resumes_parent.py`
- `tests/gateway/test_wake_admitted_before_drain_authority.py`
- `tests/gateway/test_wake_during_restart_drain_authority.py`
- `tests/hermes_cli/conftest.py`
- `tests/hermes_cli/test_hf_browse.py`
- `tests/hermes_cli/test_isolated_scope_listing.py`
- `tests/hermes_cli/test_kanban_active_pr_idle_custody.py`
- `tests/hermes_cli/test_kanban_host_manager_loss.py`
- `tests/hermes_cli/test_kanban_operator_resume_observed_feed.py`
- `tests/hermes_cli/test_kanban_operator_resume_worker_copy.py`
- `tests/hermes_cli/test_kanban_owner_death.py`
- `tests/hermes_cli/test_kanban_owner_storage_refusal.py`
- `tests/hermes_cli/test_kanban_owner_unreachable_midrun.py`
- `tests/hermes_cli/test_kanban_pickup_history.py`
- `tests/hermes_cli/test_kanban_refusal_fallback.py`
- `tests/hermes_cli/test_kanban_requeue_custody.py`
- `tests/hermes_cli/test_kanban_session_activity.py`
- `tests/hermes_cli/test_kanban_workspace_source.py`
- `tests/hermes_cli/test_load_progress.py`
- `tests/hermes_cli/test_project_plugin_rce_bypass.py`
- `tests/hermes_cli/test_session_owner_discovery_starting.py`
- `tests/hermes_cli/test_web_server_files.py`
- `tests/hermes_cli/test_webapp_uploads.py`
- `tests/plugins/test_kanban_dashboard_plugin.py`
- `tests/tui_gateway/test_readiness_singleflight.py`

These were read as part of the mirror from the source diffs only. **Source test coverage was NOT executed in this mirror run**; no pass/fail result is claimed.

## Applying

Treat this series (source ordinals 6380-6592, 24 commits: 6380, 6381, 6383, 6384, 6385, 6387, 6391, 6437, 6443, 6444, 6445, 6449, 6454, 6467, 6470, 6474, 6485, 6504, 6530, 6531, 6532, 6539, 6591, 6592) as one dependency unit: later commits fix, harden or test earlier ones, so apply them together and in ordinal order rather than cherry-picking. Series-level ordering and the overall mirror procedure are described in [README.md](README.md). Applying means mirroring source into a checkout; it does not install, activate or deploy anything.

## Gotchas

- Only the catalog-ordering change affects runtime; everything else is test code.
- Partitioned suites must keep the same collected case count; verify by comparing collected node IDs.
- Draining only owned mounts is deliberate: draining all registry entries would close mounts belonging to other fixtures.
- Also in this series: a lost-result retry fixture opt-in accounting double (unmocked controls retained), idempotent serve-cleanup handling of an already-exited child (only NoSuchProcess; permission refusals preserved), a cold-import control for partial legacy contract snapshots, and an idle-hold-cap check derived from the persisted guard event.
- The cron lifecycle-guard shard splits keep the collected case set unchanged; verify before applying with a before/after node-ID comparison.
- Nothing in this document is a deployment or live-behaviour claim.

## Why core, not a plugin?

The runtime fix lives in the gateway dispatch path, and the fixtures guard core import-time behaviour; neither can be expressed through a plugin hook.
