# Kanban merge compatibility: capability fixtures and recovery classification

## Behavior

Patches **0993, 1035, 1040** adapt upstream tests to existing fork gates rather than loosening production policy. Patch **1031** annotates a session-store read as outside the board-lockguard domain; it does not exempt board access generally.

## How it works

Directory-workspace tests stub `_profile_capabilities` with `terminal`, `file`, `git`, `host_repo`, and `kanban`, so their owner-policy, admission, recovery, toolset, and trial-model assertions can reach the intended path. This is not evidence that capability admission itself is covered by these fixtures.

Managed-worker-loss fixtures use `kanban_host_reboot.current_boot()` in their spawn fingerprint, keeping the scenario a crash on the current boot rather than a host-reboot classification. The failure-limit assertion expects agent `triage`, not a human `blocked` state.

`kanban_owner_recovery._admission_row` opens the receipt's session database read-only to inspect `session_admissions`. Its `# noqa: kanban-lockguard` marks this as a non-board handle. The fallback-error fixture separately supplies the adapter lookup and `session_id` required by the merged turn policy scope.

## Code

- `hermes_cli/kanban_owner_recovery.py::_admission_row` (1031): read-only owner URI and narrow sweep annotation.
- `tests/gateway/fixtures/kanban_recovery_probe.py::main` (0993): recovery probe capability fixture.
- `tests/hermes_cli/test_kanban_managed_worker_loss.py`: current-boot fingerprint and triage expectation.
- Capability stubs in the gateway and CLI test files listed below (0993, 1040).

## Tests

Tests identified from source, **not executed in this documentation pass**:

- `tests/gateway/test_kanban_finite_admission.py::test_kanban_submission_enters_single_query_approval_context` and its `kanban_owner` fixture; `tests/gateway/test_bare_custom_launch_keeps_profile_route.py::test_a_kanban_custom_provider_override_stays_on_the_profiles_endpoint`.
- `tests/gateway/test_fallback_failure_details.py::test_gateway_reply_includes_failed_fallback`: fixture interface adaptation, not a new Kanban behavior.
- `tests/gateway/test_session_kanban.py::test_claim_freezes_task_policy_and_rejects_forgery`, `test_claim_reclaimed_before_admission_marker_is_refused`.
- `tests/gateway/test_session_kanban_run_off_loop.py::test_run_task_kanban_write_never_blocks_the_loop`.
- `tests/hermes_cli/test_kanban_worker_spawn_toolsets.py::test_worker_toolsets_come_from_the_assignee_profile_not_the_parent_config`; `tests/hermes_cli/test_welcome_trial.py::_kanban_worker_model` is a helper adapted to grant capabilities, not itself a test function.
- `tests/hermes_cli/test_kanban_managed_worker_loss.py::test_a_killed_managed_worker_blocks_the_card_at_the_failure_limit`: despite its historical name, the modified assertion is `status == 'triage'`.
- Patch 1031 names the existing sweep `test_kanban_modules_open_boards_only_through_the_guard` in its message, but does not include that test's file or body. No new executable test is added by 1031.

## Applying

Apply as ordered members of the full fork series using `git am -3 --empty=keep` with preceding patches and the documented series base. These adaptations depend on the merged owner architecture and existing fork capability/recovery rules. Neither standalone application nor the underlying tests was executed here.

## Gotchas

Capability fixtures deliberately bypass the gate under discussion; do not cite them as permission-denial coverage. A current-boot crash fixture does not test reboot recovery. The lockguard exception is specific to session state, not a general raw-SQLite allowance.

## Plugin

No plugin is added. These are core test compatibility adjustments and a core static-check annotation.
