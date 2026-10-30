# Kanban owner execution: model pins and safe imports

## Behavior

Patches **0982, 1022, 1027, 1028** preserve fork behavior after execution moves into the profile owner: the current run's model tier reaches the owner, class selectors use provider `auto`, and the receipt-waiting worker avoids importing a checkout from its workspace cwd. The placement forwarder's fallback test now expects that owner-client worker, not `chat -q`.

## How it works

`owner_model_pin(conn, task)` starts with persisted overrides, then reads the latest `model_tier` event for exactly `task.id` and `current_run_id`. A valid event model replaces the row pin; malformed payloads fall back to the row. `worker_model_args` supplies the existing class/provider rules. `gateway.session_kanban.build_kanban_policy` uses the resulting model/provider, while reasoning remains the card's value. Tier decisions do not rewrite the task's persistent model override.

`kanban_db_dispatch._worker_argv` uses `cron.scheduler_worker_env.hermes_module_argv` to launch Python with `-P -m hermes_cli.kanban_worker_client`. The owner executes the turn; the client waits for its receipt. Routing tests construct the actual agent through `tui_gateway.server._make_agent` rather than the retired CLI-local startup path.

## Code

- `hermes_cli/kanban_model_tier.py`: `owner_model_pin`, existing `worker_model_args`.
- `gateway/session_kanban.py`: `build_kanban_policy`.
- `hermes_cli/kanban_db_dispatch.py`: `_worker_argv`.
- `tests/hermes_cli/test_kanban_class_routing.py`: `owner_pin`, `gateway_agent` test helpers.

## Tests

Tests identified from source, **not executed in this documentation pass**:

- `tests/hermes_cli/test_kanban_model_tier.py::test_tier_pin_reaches_owner`: event-backed pin, unchanged persisted override, and isolation from another run; `test_owner_runs_class_selector_with_provider_auto`: class provider rule.
- `tests/plugins/test_kanban_model_override.py::test_spawn_resolves_claimed_task_policy`: owner policy from the claimed task, including an event-backed tier.
- `tests/hermes_cli/test_kanban_class_routing.py::test_class_tiers_keep_strong_work_deep`, `test_dispatch_tick_reads_class_default_and_keeps_review_deep`, `test_explicit_pin_survives_review_class_default`, `test_invalid_class_provider_refused_before_launch`: migrated owner-pin assertions.
- Same file, `test_worker_generated_args_reach_native_wire`: mocked native transport and exhausted-credential rejection through gateway construction; not a live provider request.
- `tests/hermes_cli/test_kanban_class_unroutable.py::test_unroutable_class_tier_keeps_profile_model`, `test_routable_class_tier_is_kept`: dispatcher outcome through owner pins.
- `tests/hermes_cli/test_cli_class_routing.py::test_cli_real_construction_request_and_database_resume`: gateway construction plus CLI launch/restore assertions; `tests/hermes_cli/test_managed_entry.py::test_paid_entry_rejected_before_send` parameterizes CLI and Kanban model inputs, with network send mocked.
- `tests/hermes_cli/test_worker_argv_ignores_cwd_checkout.py::test_kanban_worker_in_a_checkout_workspace_imports_the_install`, `test_placement_forwarder_and_its_worker_import_the_install`: decoy cwd import probes.
- `tests/hermes_cli/test_kanban_gateway_restart_handoff.py::test_managed_gateway_worker_is_spawned_in_restart_safe_scope`, `test_standalone_dispatcher_keeps_direct_worker_spawn`, `test_oneshot_unit_dispatcher_scope_wraps_or_warns_never_dooms_silently`: exact argv adaptations.
- `tests/hermes_cli/test_kanban_placement.py::test_dispatcher_spawns_the_forwarder_for_a_deferred_card`: fallback module assertion (1028).

## Applying

These are ordered members of the full fork patch series; replay with `git am -3 --empty=keep` from the series' documented base and include preceding patches. The gateway owner cutover, model-tier machinery, and safe module-argv helper are prerequisites. Individual application or runtime success was not tested here.

## Gotchas

A worker's `-m` now names a Python module, not a model. Do not recover model routing by parsing its argv. `-P` prevents implicit cwd insertion; it does not sanitize arbitrary explicit `PYTHONPATH` entries. The tests above use isolated fixtures and mocked transports, not a deployed gateway/provider integration.

## Plugin

Core owner-policy and process-launch changes cannot be supplied solely by a plugin. Model-override plugin tests check compatibility with the core execution path; no plugin installation is required.
