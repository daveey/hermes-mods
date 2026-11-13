# Proof-home isolation for restart-proof backends

Patches: **3051, 3052, 3053, 3063, 3082**.

## Behavior
- `hermes serve|dashboard --proof-home DIR` (or `HERMES_PROOF_HOME`) selects a disposable `HERMES_HOME` during bootstrap, before config, scratch export or startup recovery. DIR must exist and must not be the production root, a production profile, or the launching agent's home; it cannot be combined with `--profile`. Invalid input exits 2 with a safe recipe before any write.
- An agent-originated restart-proof backend shape on a production home exits 2 instead of adopting unrelated chats. A restart-proof shape is `--isolated` or a Desktop-owned backend (Desktop env plus the session token env or the SSH token-file argv, including the `--flag=PATH` form).
- Plain `dashboard`/`serve` run by a Desktop chat's tool child is still allowed; `--status`, `--stop` and `--help` pass.
- Serve parsers reject abbreviated flags so argv checks and the parser agree.
- Desktop launches (update relaunch, desktop/gui commands, Electron startup) drop the agent provenance stamps; `serve` and `dashboard` keep them.

## How it works
- 3051 adds `hermes_cli/proof_home.py`, hooks it into `hermes_bootstrap`, re-checks in `web_server` before server consumers start, stamps tool children (terminal, process, execute_code) with `HERMES_AGENT_TOOL_CALL`, `HERMES_AGENT_ORIGIN`, `HERMES_AGENT_HOME`, and makes startup resume and process re-adoption refuse production homes.
- 3052 drops those stamps in `clean_launch_env`, on desktop/gui argv and at Electron startup; a vitest pins the Electron key list and a Python test pins `PROVENANCE_KEYS`.
- 3053 keys the refusal on Desktop backend ownership (`is_desktop_owned_backend`) rather than inherited `HERMES_DESKTOP`.
- 3063 makes `is_desktop_ssh_backend_argv` match the equals form and pins both key lists literally.
- 3082 builds both serve parsers with `allow_abbrev=False`.

## Code
- `hermes_cli/proof_home.py`
- `hermes_bootstrap.py`
- `hermes_cli/top_level_env.py`
- `hermes_cli/_startup_fast.py`
- `hermes_cli/subcommands/dashboard.py`
- `hermes_cli/web_server.py`
- `apps/desktop/electron/leaked-worker-env.ts`
- `apps/desktop/electron/main.ts`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_serve_proof_home.py`: `test_agent_production_home_rejected_before_config`, `test_wrapped_tool_origin_rejected_without_worker_vars`, `test_desktop_env_without_isolated_still_rejected`, `test_profile_alias_of_production_rejected`, `test_tool_child_stamp_without_worker_vars`, `test_ssh_backend_reexports_origin_remotely`, `test_explicit_proof_home_selected_before_config`, `test_invalid_proof_home_is_read_only`, `test_proof_home_with_profile_flag_rejected`, `test_existing_backend_contracts_reach_config`, `test_web_server_recheck_refuses_unselected_proof_home`, `test_desktop_relaunch_env_drops_tool_stamps`, `test_desktop_top_level_launch_drops_tool_stamps`, `test_backend_launch_keeps_tool_stamps`, `test_desktop_backend_shape_without_isolated_still_rejected`, `test_desktop_chat_tool_child_may_run_plain_backend`, `test_desktop_drop_list_is_the_provenance_stamps`, `test_abbreviated_token_file_flag_is_rejected_by_parser` (list is partial for the first patch).
- `tests/hermes_cli/test_serve_proof_home_recovery.py`: `test_recovery_refuses_production_before_config`.
- `tests/hermes_cli/test_process_identity.py`: `test_desktop_ssh_backend_token_file_equals_form_is_desktop_owned`.
- `tests/hermes_cli/test_fast_serve_launch.py`: `test_serve_parsers_reject_abbreviated_token_file_flag`.
- `apps/desktop/electron/leaked-worker-env.test.ts`: vitest cases pinning the Electron provenance key list and startup scrub (test names not enumerated here).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
The stamp means "spawned by a Hermes agent process"; it is not proof the caller is a person. With stamps absent, Desktop and always-on backends behave as before.
