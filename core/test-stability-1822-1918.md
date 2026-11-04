# Test stability 1822–1918

Patches: **1868** (`f1cbd59d69`, `test: write live-test child logs through O_APPEND and read them by path (#781)`), **1870** (`2ac988f883`, `test: optional-extra imports skip, not abort collection, in a minimal venv`), **1871** (`6e0d15a0e7`, `test: put each optional-extra guard above the module's first aiohttp/acp import`), **1872** (`af424f2dc9`, `test: cold follower reads its own scope on the rest of its budget [daveey]`), **1882** (`ced211b8a8`, `test(conftest): HERMES_TEST_REQUIRE_EXTRAS fails a listed extra's importorskip instead of skipping [daveey]`), **1883** (`70c571949c`, `test(conftest): add unittest-discovery guard to test_conftest_required_extras.py [daveey]`), **1892** (`381e2ff5c3`, `[daveey] test(conftest): monkeypatch restores Starlette State attributes on pytest 9.2`), **1905** (`a4d1b7260d`, `[daveey] test(resolution): allowlist the persist-scope systemd-run lookup`), **1906** (`35832b3fd0`, `[daveey] test: fix two red-main tests (Wednesday compaction boundary; 6/7 claimers counter race) (#817)`), **1911** (`5d2c46c26f`, `test(gateway): give compress-case turns a 180 s hang bound, not 30 s`), **1914** (`bb0c85f52d`, `test(rendezvous): check brokered ends by socket identity, not fd number [daveey] (#823)`).

Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

## Behavior

- Live-test child logs are written through O_APPEND and read by path (1868).
- Optional-extra imports (`keyring`, `aiohttp`, `acp`) skip instead of aborting collection in a minimal venv, with each guard above the module's first such import (1870/1871); `HERMES_TEST_REQUIRE_EXTRAS` turns a listed extra's skip into a failure in lanes that install it (1882/1883).
- A cold annotation follower reads its own scope on the rest of its budget (1872); monkeypatch restores Starlette `State` attributes on pytest 9.2 (1892).
- The persist-scope `systemd-run` lookup is allowlisted (1905); two red-main tests fixed (Wednesday compaction boundary, claimer counter race) (1906); compress-case turns get a 180 s hang bound (1911); brokered socket ends are checked by identity, not fd number (1914).

## How it works

Test-only and CI-runner changes (`tests/conftest.py`, `scripts/run_tests.sh`, `.github/workflows/tests.yml`).

Source commit notes (first paragraph of each message, privacy-normalized):

- 1868: * test: write live-test child logs through O_APPEND and read them by path
- 1870: `pytest tests/hermes_cli` stopped at collection in any venv without the `keychain` extra (the hub's production venv): test_vault_key_cli.py imported `keyring` at module top, and one collection error interrupts the whole session. Whole-tree collection had 149 such errors (keyring, aiohttp, acp, plus one lark_oapi ordering bug).
- 1871: In 35 files the guard sat below a 'from aiohttp import web' (or an acp_adapter import), because the collect log only showed the line that failed after another test's MagicMock stub let the first import through. Collected alone in a venv without aiohttp, those files still errored (e.g. pytest --collect-only tests/tui_gateway, tests/gateway/test_managed_entry.py). Each of the 139 touched test files now collects alone in the hub production venv (rc 0 or 5).
- 1872: PR #699 bounded cold annotation polls to one _REFRESH_WAIT_S, but two mutations of the follower branch survived the related 28-file set: an inline (unbounded) follower read (M3) and a fresh full budget for it (M5). The cold test kept the leader's read blocked for the whole window, so no follower ever reached its own read.
- 1882: #762 (6e0d15a0e7) guarded 139 test files with module-level pytest.importorskip for aiohttp.test_utils, acp and keyring so a minimal venv collects. In a CI lane that installs those extras, the same guard would silently skip the files if an extra were dropped from the install, with CI green.
- 1883: test(conftest): add unittest-discovery guard to test_conftest_required_extras.py [daveey]
- 1892: pytest 9.2's MonkeyPatch.setattr reads the value to restore from the target's instance __dict__. Starlette's State (app.state, request.state) keeps attributes in State._state behind __getattr__/__setattr__, so every setattr on it records "no old value": a double setattr of one attribute deletes it twice at undo (KeyError at teardown), and an attribute the app already had is deleted instead of restored. Under a pytest 9.2.0 overlay 10 test files errored at teardown (test_webapp_gated_sign_out also failed one test, test_webapp_socket_tickets took 125 s instead of 2.4 s); the lock pins 9.0.3,...
- 1905: [daveey] test(resolution): allowlist the persist-scope systemd-run lookup
- 1906: * [daveey] test(gateway): derive the compacted turns in the compress/restart FIFO journey
- 1911: A managed_workers turn spawns a fresh agent.managed_worker interpreter, so its wall time is CPU-bound: ~7-14 s per turn on a loaded hub and 25-28 s when the process tree only gets 25% of the CPU (SIGSTOP/SIGCONT 0.3 s/0.1 s duty cycle). The 30 s per-turn deadline then expires, exactly as on CI run 41627985551 ("timed out after 30s waiting for HISTORY_3 terminal"). The poll returns as soon as the admission is terminal, so the larger bound only matters on a hang.
- 1914: test_authenticated_listener_receives_fd_and_child_gets_start asserted the owner kept no copy of the child's socketpair end with os.fstat(<number>). The owner closes that end right after Popen, so the number is free, and the next allocation in the process (the in-test acceptor's recv_fds of the parent end, or any other thread) reuses it. In CI, earlier tests in the file leave the lower fds occupied, so the number was often reused and the assert failed with _fd_open(18/19) == True (4 CI flakes on 11-03).

## Code

`.github/workflows/tests.yml`, `scripts/run_tests.sh`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/_fixtures/required_extras.py`
- `tests/_fixtures/state_monkeypatch.py`
- `tests/acp_adapter/test_acp_commands.py`
- `tests/acp_adapter/test_acp_discard_review.py`
- `tests/acp_adapter/test_acp_image_strict_reject.py`
- `tests/acp_adapter/test_acp_images.py`
- `tests/acp_adapter/test_acp_mcp_discovery.py`
- `tests/acp_adapter/test_acp_reattach_unknown_notice.py`
- `tests/acp_adapter/test_acp_unacked_identity_retry.py`
- `tests/acp_adapter/test_adversarial_unknown_follower.py`
- `tests/acp_adapter/test_entry.py`
- `tests/acp_adapter/test_events.py`
- `tests/acp_adapter/test_finish_turn_finally.py`
- `tests/acp_adapter/test_gateway_attach_mode_race.py`
- `tests/acp_adapter/test_gateway_attach_shutdown.py`
- `tests/acp_adapter/test_gateway_cancel_admission.py`
- `tests/acp_adapter/test_gateway_client_boundary.py`
- `tests/acp_adapter/test_gateway_config_options.py`
- `tests/acp_adapter/test_gateway_config_projection.py`
- `tests/acp_adapter/test_gateway_controls.py`
- `tests/acp_adapter/test_gateway_delivery_shapes.py`
- `tests/acp_adapter/test_gateway_entry_lifecycle.py`
- `tests/acp_adapter/test_gateway_event_mapping.py`
- `tests/acp_adapter/test_gateway_launch_flags.py`
- `tests/acp_adapter/test_gateway_model_provider.py`
- `tests/acp_adapter/test_gateway_prompt_content.py`
- `tests/acp_adapter/test_gateway_prompt_reoffer.py`
- `tests/acp_adapter/test_gateway_queued_unknown.py`
- `tests/acp_adapter/test_gateway_reasoning.py`
- `tests/acp_adapter/test_gateway_reconnect.py`
- `tests/acp_adapter/test_gateway_remote_image_submit.py`
- `tests/acp_adapter/test_gateway_replay_gap.py`
- `tests/acp_adapter/test_gateway_retry_command.py`
- `tests/acp_adapter/test_gateway_session_commands.py`
- `tests/acp_adapter/test_gateway_slash_text.py`
- `tests/acp_adapter/test_gateway_snapshot_adoption.py`
- `tests/acp_adapter/test_gateway_submit_retry.py`
- `tests/acp_adapter/test_gateway_terminal_projection.py`
- `tests/acp_adapter/test_gateway_terminal_receipt_claim.py`
- `tests/acp_adapter/test_gateway_tool_projection.py`
- `tests/acp_adapter/test_gateway_unacked_slash.py`
- `tests/acp_adapter/test_gateway_voice_stopped.py`
- `tests/acp_adapter/test_gateway_wire_controls.py`
- `tests/acp_adapter/test_mcp_e2e.py`
- `tests/acp_adapter/test_named_provider_catalogs.py`
- `tests/acp_adapter/test_permissions.py`
- `tests/acp_adapter/test_ping_suppression.py`
- `tests/acp_adapter/test_retained_identity_retirement.py`
- `tests/acp_adapter/test_runtime_identity.py`
- `tests/acp_adapter/test_server.py`
- `tests/acp_adapter/test_session_construction_off_loop.py`
- `tests/acp_adapter/test_tools.py`
- `tests/agent/test_vault_key_storage.py`
- `tests/child_log.py`
- `tests/conftest.py`
- `tests/fakes/platforms/_standin.py`
- `tests/fakes/platforms/discord_standin.py`
- `tests/fixtures/resolution_allowlist.json`
- `tests/gateway/fixtures/local_recovery_probe.py`
- `tests/gateway/fixtures/owner_process.py`
- `tests/gateway/test_adversarial_responses_eviction.py`
- `tests/gateway/test_adversarial_responses_pending.py`
- `tests/gateway/test_api_admission_off_loop.py`
- `tests/gateway/test_api_authority_daemon.py`
- `tests/gateway/test_api_authority_store.py`
- `tests/gateway/test_api_canonical_projection.py`
- `tests/gateway/test_api_chat_run_owner.py`
- `tests/gateway/test_api_class_declared_identity.py`
- `tests/gateway/test_api_compressed_receipt_history.py`
- `tests/gateway/test_api_context_usage.py`
- `tests/gateway/test_api_crash_recovery.py`
- `tests/gateway/test_api_cutover_results.py`
- `tests/gateway/test_api_doors_logical_owner.py`
- `tests/gateway/test_api_hosted_daemon.py`
- `tests/gateway/test_api_idempotency_after_delete.py`
- `tests/gateway/test_api_idempotency_namespace.py`
- `tests/gateway/test_api_image_delete_scale.py`
- `tests/gateway/test_api_implicit_session_after_delete.py`
- `tests/gateway/test_api_model_lock_fallback.py`
- `tests/gateway/test_api_observer_gone_session.py`
- `tests/gateway/test_api_pending_daemon.py`
- `tests/gateway/test_api_request_one_target.py`
- `tests/gateway/test_api_responses_replay_frozen_tool_events.py`
- `tests/gateway/test_api_responses_store_off_loop.py`
- `tests/gateway/test_api_responses_stream_retry.py`
- `tests/gateway/test_api_runs_poll_shape.py`
- `tests/gateway/test_api_server.py`
- `tests/gateway/test_api_server_active_work_drain.py`
- `tests/gateway/test_api_server_compaction_projection.py`
- `tests/gateway/test_api_server_declared_conversation.py`
- `tests/gateway/test_api_server_jobs.py`
- `tests/gateway/test_api_server_model_options_include_unconfigured.py`
- `tests/gateway/test_api_server_multimodal.py`
- `tests/gateway/test_api_server_profile_prefix_misdelivery.py`
- `tests/gateway/test_api_server_reasoning_nonstream.py`
- `tests/gateway/test_api_server_runs.py`
- `tests/gateway/test_api_server_runs_hosted_room.py`
- `tests/gateway/test_api_server_runs_import_isolation.py`
- `tests/gateway/test_api_server_runs_room_policy_grant.py`
- `tests/gateway/test_api_server_sse_keepalive.py`
- `tests/gateway/test_api_server_transform_llm_output.py`
- `tests/gateway/test_api_session_create_after_delete.py`
- `tests/gateway/test_api_session_preflight.py`
- `tests/gateway/test_api_synthetic_nudge_receipt.py`
- `tests/gateway/test_api_terminal_http.py`
- `tests/gateway/test_api_unknown_resolution.py`
- `tests/gateway/test_bot_receipt_tombstone_expiry.py`
- `tests/gateway/test_chat_completions_approval.py`
- `tests/gateway/test_cron_fire_startup_gate.py`
- `tests/gateway/test_cron_fire_webhook.py`
- `tests/gateway/test_delete_session_no_resurrect.py`
- `tests/gateway/test_derived_generation_after_sweep.py`
- `tests/gateway/test_feishu_approval_buttons.py`
- `tests/gateway/test_feishu_card_action_gateway_authz.py`
- `tests/gateway/test_hold_stream_api_server.py`
- `tests/gateway/test_journey_compress_restart_fifo.py`
- `tests/gateway/test_managed_entry.py`
- `tests/gateway/test_multiplex_api_server_routing.py`
- `tests/gateway/test_multiplex_cold_profile_sidebar_mutation.py`
- `tests/gateway/test_multiplex_shared_ingress.py`
- `tests/gateway/test_multiplex_toolsets_profile_isolation.py`
- `tests/gateway/test_mute_live_owner.py`
- `tests/gateway/test_native_http_auth.py`
- `tests/gateway/test_native_http_per_profile_edits.py`
- `tests/gateway/test_native_http_secondary_profile_binding.py`
- `tests/gateway/test_native_ingress_media.py`
- `tests/gateway/test_native_profile_registry_grant.py`
- `tests/gateway/test_normal_runtime_boot.py`
- `tests/gateway/test_owner_backfill_live_owner.py`
- `tests/gateway/test_peer_dm_bot_chat_authority.py`
- `tests/gateway/test_peer_dm_bot_chat_headless.py`
- `tests/gateway/test_peer_dm_into_open_bot_chat.py`
- `tests/gateway/test_peer_dm_transient_retry.py`
- `tests/gateway/test_raft_adapter.py`
- `tests/gateway/test_responses_chain_api_content.py`
- `tests/gateway/test_responses_compressed_chain.py`
- `tests/gateway/test_responses_disconnected_identity.py`
- `tests/gateway/test_responses_idem_replay_record.py`
- `tests/gateway/test_responses_identity_migration.py`
- `tests/gateway/test_responses_identity_retention.py`
- `tests/gateway/test_responses_output_order.py`
- `tests/gateway/test_responses_request_identity.py`
- `tests/gateway/test_retained_response_chain_after_delete.py`
- `tests/gateway/test_runtime_token_identity.py`
- `tests/gateway/test_secondary_route_fence_scope.py`
- `tests/gateway/test_session_api.py`
- `tests/gateway/test_session_authority.py`
- `tests/gateway/test_session_kanban_rendezvous.py`
- `tests/gateway/test_session_mutation_compress.py`
- `tests/gateway/test_session_ws_upgrade_failure_logged.py`
- `tests/gateway/test_unified_gateway_native_live.py`
- `tests/gateway/test_webhook_adapter.py`
- `tests/gateway/test_webhook_cron_trigger.py`
- `tests/gateway/test_webhook_deliver_only.py`
- `tests/gateway/test_webhook_durable_destination.py`
- `tests/gateway/test_webhook_finalization_recovery.py`
- `tests/gateway/test_webhook_integration.py`
- `tests/gateway/test_webhook_mcp_events.py`
- `tests/gateway/test_webhook_replay_scope.py`
- `tests/gateway/test_webhook_route_toolsets.py`
- `tests/gateway/test_webhook_signature_rate_limit.py`
- `tests/gateway/test_webhook_startup_restore_admission.py`
- `tests/gateway/test_worker_lifecycle_execution.py`
- `tests/gateway/test_worker_persistence.py`
- `tests/gateway/test_worker_rotation_adoption.py`
- `tests/hermes_cli/test_gateway_snapshot_restore_live.py`
- `tests/hermes_cli/test_kanban_annotation_cold_bounded.py`: `test_cold_follower_reads_its_scope_on_what_is_left_of_its_budget`
- `tests/hermes_cli/test_kanban_hub_admission.py`
- `tests/hermes_cli/test_vault_key_cli.py`
- `tests/test_child_log.py`: `test_child_writes_land_at_eof_even_after_the_shared_offset_moves`
- `tests/test_conftest_required_extras.py`: `test_listed_missing_module_raises_and_names_it`, `test_unlisted_missing_module_still_skips`, `test_listed_present_module_is_returned`, `test_unset_leaves_importorskip_untouched`, `test_install_matches_top_level_names_and_does_not_stack`, `test_collection_through_conftest`, `test_never`
- `tests/test_conftest_state_monkeypatch.py`: `test_fixture_is_still_a_pytest_monkeypatch`, `test_double_setattr_of_new_state_attribute_undoes_cleanly`, `test_double_setattr_of_existing_state_attribute_restores_original`, `test_app_state_attribute_survives_patching`, `test_delattr_then_setattr_of_existing_state_attribute_restores_original`, `test_setattr_then_delattr_of_existing_state_attribute_restores_original`, `test_delattr_of_missing_state_attribute_raises_unless_raising_false`, `test_missing_state_attribute_raises_unless_raising_false`, `test_non_state_targets_keep_native_behaviour`
- `tests/tui_gateway/test_hosted_room_two_gateway_scoped.py`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
