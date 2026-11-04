# Kanban: trusted capsule admission for host_repo worktree cards on isolated backends

Patches: **1861** (`f78fd0940f`, `kanban: trusted capsule admission for host_repo worktree cards on isolated backends`), **1862** (`71787a8a8e`, `kanban: capsule card submitter refuses an owner without this core's capsule gates`), **1863** (`3d99cc66ce`, `kanban: capsule-bound workers get a truthful /workspace contract and cwd [daveey]`), **1864** (`ee936646d2`, `kanban: capsule contract maps host-run plan steps and the no-change hand-off [daveey]`), **1865** (`1460c200ab`, `kanban: capsule worker-context rewrite locates the Workspace header by structure, fails closed [daveey]`), **1866** (`0d29d77d59`, `kanban: capsule stack on main — fix #642's CI failures, health-clean split [daveey]`).

Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

## Behavior

- A host-owned, in-process capsule registration lets a docker/runsc builder whose worktree becomes a task-local capsule be assigned `host_repo` worktree cards (1861).
- The capsule card submitter refuses an owner that lacks this core's capsule gates (admission probe, single-use rendezvous, `--observation-fd`) instead of running an ordinary managed worker (1862).
- Capsule-bound workers get a truthful `/workspace` contract and cwd (1863), the contract maps host-run plan steps and the no-change hand-off (1864), and the worker-context rewrite locates the Workspace header by structure and fails closed (`capsule_context_header_drift`) (1865).
- The stack lands on main with the CI fixes and health-clean splits (1866).

## How it works

`hermes_cli/kanban_capsule_admission.py` binds the registration to the board dispatch owner, profile homes, project id and repo root; `hermes_cli/kanban_capsule_owner.py` checks owner gates; `agent/agent_init_capsule.py`, `gateway/session_kanban_capsule.py` and `tools/kanban_tools_capsule.py` shape the worker contract.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1861: Native create and dispatch require host_repo for every worktree card and refuse remote terminal backends, so a docker/runsc builder whose worktree is turned into a task-local capsule could never be assigned one. This adds a host-owned, in-process capsule registration (hermes_cli/kanban_capsule_admission.py) bound to the board dispatch owner, exact profile homes, project id, repo root and a real (non-stock) spawn transport. Native create consumes it via a create scope; the dispatcher consumes it at claim and launch, and the launch only proceeds through an authenticated, single-use owner rend...
- 1862: A capsule-required card's gates (admission probe, single-use rendezvous, --observation-fd) live in the profile owner. kanban_worker_client reached whatever owner discovery returned; a named profile is served by the root multiplexer, so a pinned-core submitter reached a global-core owner with no capsule gates, which admitted the card and ran an ordinary managed worker (hub 2026-11-02, run4 of t_01a19a6b4fcce737).
- 1863: A capsule-bound Kanban worker (tools.capsule_workspace bound by observation_bind) only has the task's private git checkout mounted at /workspace in a runsc sandbox, and the host publishes its committed head. Run6 (Ask Eve) showed every model-facing surface said otherwise: KANBAN_GUIDANCE told it to cd $HERMES_KANBAN_WORKSPACE (unset in the sandbox) and to kanban_complete when a review child exists, the first turn's Workspace header named the HOST worktree, and terminal commands ran in the profile's terminal.cwd (/root). The worker probed host paths and blocked asking for a host backend and...
- 1864: Run6's approved plan says 'fetch and inspect origin/main' and later review/CI/publish steps; the contract now says the host runs those around the worker and /workspace HEAD is the host-fetched base, so the plan text stays as approved without telling the worker to fetch. A no-change task hands off without a commit and with metadata no_change.
- 1865: Pinned review P1/P2: the capsule rewrite searched the rendered context for the header, so a title holding '## Body' or a forged 'Workspace:' line could shadow it and leave the host path in the prompt. The owner now computes the header line's exact span from _ctx_header's line list (ctx_workspace_span) and the rewrite replaces only that span; a missing or non-matching span raises capsule_context_header_drift.
- 1866: Lands the capsule admission/owner-binding/worker-contract stack on main and fixes what failed daveey/hermes#642 (run 41409661890, 7 of 8 Python shards red).

## Code

`agent/chat_completion_helpers.py`, `agent/client_lifecycle.py`, `agent/codex_runtime.py`, `agent/managed_worker.py`, `agent/observation_bind.py`, `agent/observation_load.py`, `agent/observation_seam.py`, `agent/session_persistence.py`, `agent/tool_executor.py`, `agent/turn_api_call.py`, `gateway/session_kanban.py`, `gateway/session_kanban_capsule.py`, `gateway/session_kanban_rendezvous.py`, `gateway/session_kanban_scope.py`, `hermes_cli/kanban_capabilities.py`, `hermes_cli/kanban_capsule_admission.py`, `hermes_cli/kanban_db.py`, `hermes_cli/kanban_db_creation.py`, `hermes_cli/kanban_db_dispatch.py`, `hermes_cli/kanban_db_dispatch_admission.py`, `hermes_cli/kanban_gateway_drain.py`, `hermes_cli/main.py`, `tools/capsule_workspace.py`, `tools/code_execution_tool.py`, `tools/environments/docker.py`, `tools/terminal_tool_backends.py`, `gateway/run_runtime.py`, `hermes_cli/gateway_client.py`, `hermes_cli/kanban_capsule_owner.py`, `hermes_cli/kanban_worker_client.py`, `agent/agent_init.py`, `agent/agent_init_capsule.py`, `agent/prompt_builder.py`, `tools/kanban_show.py`, `tools/kanban_tools_capsule.py`, `tools/terminal_tool.py`, `hermes_cli/kanban_db_context.py`, `agent/chat_completion_helpers_reasoning.py`, `hermes_cli/kanban_db_dispatch_preclaim.py`, `hermes_cli/main_model_arg.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/agent/test_managed_worker_observation_bind.py`: `test_argv_flag_is_strict`, `test_no_channel_ordinary_card_is_a_noop`, `test_no_channel_required_card_refuses`, `test_owner_brokered_channel_binds_workspace_real_child`, `test_wrong_start_refuses_real_child`, `test_malformed_start_line_refuses_real_child`, `test_closed_channel_without_start_refuses_real_child`, `test_self_made_channel_is_not_owner_supplied`, `test_execute_binds_workspace_after_context_before_recovery_hooks_and_mcp`, `test_execute_start_never_arrives_raises_before_tool_discovery`, `test_execute_malformed_start_raises_before_tool_discovery`, `test_main_refuses_bad_observation_argv_before_hello`
- `tests/agent/test_managed_worker_observer_load.py`: `test_digest_golden_vector_shared_with_integration`, `test_verified_observer_loaded_installed_and_sealed_real_child`, `test_failed_run_is_poisoned_and_unsealed_real_child`, `test_untrusted_or_missing_observer_refuses_before_start_real_child`, `test_start_sender_not_host_pid_refuses_before_bind_real_child`, `test_ordinary_card_never_imports_observer_real_child`, `test_ancestor_rule_units`, `test_execute_success_seals_before_revoke`, `test_execute_failure_is_poisoned_and_unsealed`
- `tests/agent/test_observation_seam.py`: `test_core_hook_modules_import_without_observer`, `test_unobserved_hooks_pass_through`, `test_observed_launch_delegates_to_loaded_observer`
- `tests/gateway/test_managed_worker_unknown_status.py`
- `tests/gateway/test_session_kanban_capsule_guard.py`: `test_remote_host_repo_requirement_is_derived_and_frozen`, `test_local_or_scratch_remote_not_required`, `test_required_capsule_without_transport_fails_closed`, `test_old_or_replayed_saved_policy_rederives`, `test_unreadable_backend_policy_fails_closed`, `test_no_config_or_param_grant_accepted`, `test_launch_guard_refuses_before_worker_spawn`, `test_launch_guard_ordinary_card_spawns`, `test_admission_refuses_before_session_and_owner_admitted`
- `tests/gateway/test_session_kanban_rendezvous.py`: `test_authenticated_listener_receives_fd_and_child_gets_start`, `test_fingerprint_matches_worker_bound_shape`, `test_probe_is_ready_only_with_authenticated_listener_and_does_not_consume`, `test_socket_dir_not_0700_refuses`, `test_symlinked_socket_dir_refuses`, `test_wrong_listener_uid_refuses`, `test_dir_or_socket_not_owned_by_owner_refuses`, `test_non_socket_at_run_path_refuses`, `test_identity_mismatch_at_hello_refuses_before_spawn`, `test_listener_refusing_fd_kills_child`, `test_delivery_failure_after_spawn_kills_child`, `test_no_rendezvous_required_card_refuses_and_no_child_survives` (+25 more)
- `tests/hermes_cli/test_gateway_client_backlog.py`
- `tests/hermes_cli/test_kanban_capsule_admission.py`: `test_scoped_create_admits_exact_project_worktree`, `test_docker_worktree_refused_without_registration`, `test_registered_but_unscoped_or_revoked_create_refuses`, `test_scoped_create_outside_exact_scope_refuses`, `test_scoped_create_wrong_repo_refuses`, `test_scoped_create_on_another_board_refuses`, `test_owner_change_revokes_create`, `test_other_capabilities_are_still_validated`, `test_scope_is_thread_local`, `test_no_capability_leakage_outside_create`, `test_handle_is_live_only_and_not_serializable`, `test_registration_refuses_mismatched_binding` (+14 more)
- `tests/hermes_cli/test_kanban_capsule_claim_race.py`: `test_ready_row_mutated_between_admission_and_claim_is_refused`, `test_review_row_mutated_between_admission_and_claim_is_refused`, `test_unmutated_row_still_claims_through_the_recheck`, `test_review_lane_dispatches_registered_capsule`, `test_review_reassignment_cannot_carry_host_repo`, `test_symlinked_worktrees_root_is_refused_before_effects`, `test_symlink_swapped_in_before_claim_is_refused`
- `tests/hermes_cli/test_kanban_capsule_lifetime.py`: `test_copied_context_cannot_create_after_scope_exit`, `test_async_child_cannot_create_after_scope_exit`, `test_expired_scope_cannot_be_revived_by_a_new_scope`, `test_forked_child_has_no_live_handle_or_scope`
- `tests/hermes_cli/test_kanban_capsule_owner_binding.py`: `test_required_card_refuses_unbound_owner_before_ticket`, `test_required_card_reaches_bound_owner`, `test_ordinary_card_unchanged_with_any_owner`, `test_unreadable_board_or_task_fails_closed`, `test_submitter_without_code_identity_refuses`, `test_explicit_endpoint_and_remote_url_paths_check`, `test_submit_exit_is_a_counted_refusal_not_an_owner_wait`, `test_capsule_core_owner_advertises_capability`
- `tests/hermes_cli/test_kanban_worker_owner_wait.py`
- `tests/hermes_cli/test_kanban_worker_socket_recovery.py`
- `tests/hermes_cli/test_kanban_worker_start_failure_cause.py`
- `tests/tools/capsule_fixtures.py`
- `tests/tools/conftest.py`
- `tests/tools/test_capsule_code_execution.py`: `test_unbound_local_execute_code_uses_the_host_kernel`, `test_bound_local_execute_code_never_reaches_the_host_kernel`, `test_revoked_binding_local_execute_code_never_reaches_the_host_kernel`
- `tests/tools/test_capsule_worker_contract.py`: `test_unbound_docker_default_cwd_stays_the_profile_cwd`, `test_bound_docker_default_cwd_is_the_capsule`, `test_bound_non_docker_config_is_not_rewritten`, `test_real_terminal_tool_command_cwd`, `test_bound_terminal_keeps_a_cd_within_the_capsule`, `test_bound_terminal_explicit_workdir_is_honoured`, `test_file_ops_env_starts_in_capsule`, `test_capsule_guidance_replaces_every_host_workspace_and_completion_line`, `test_capsule_guidance_fails_closed_on_generic_drift`, `test_agent_init_selects_capsule_guidance_only_when_bound`, `test_worker_context_header_names_the_capsule_only_when_bound`, `test_kanban_show_own_card_names_execution_workspace_when_bound` (+3 more)
- `tests/tools/test_capsule_workspace.py`: `test_bind_returns_process_local_handle_that_cannot_be_serialized`, `test_bind_refuses_identity_that_differs_from_worker_env`, `test_bind_refuses_when_worker_env_missing`, `test_bind_refuses_noncanonical_or_foreign_profile_home`, `test_bind_refuses_noncanonical_workspace`, `test_bind_refuses_unless_liveness_is_true`, `test_one_binding_per_process_even_after_close`, `test_env_and_config_alone_grant_nothing`, `test_without_binding_ordinary_backends_are_unchanged`, `test_bound_create_environment_mounts_only_the_capsule`, `test_each_tool_entry_gets_the_capsule`, `test_each_tool_entry_refuses_after_revocation` (+17 more)

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
