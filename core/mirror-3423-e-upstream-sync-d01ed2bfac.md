# Upstream sync d01ed2bfac: merge fixups and code-health ratchets

Patches: **3185–3197, 3209**.

Replay note: 3185 is preceded in series order by an upstream-sync bridge (upstream `d01ed2bfac` via a fork merge); 3185 itself is the fork's first fixups commit after that merge.

## Behavior
- Hosted reads skip the host-home guard: the `HOME_READ_DENIED_DIRS` loop no longer resolves host home paths for a sandbox path, satisfying upstream's sandbox-conformance rule (3185). The composed worker test advances turn generation 0 first, since upstream's turn fence refuses generation 0.
- No behavior change from the code-health moves: `_caller_identity_for_target` lives in `hermes_cli/kanban_notify_identity.py`, a shared `authority_cwd_in_tmp` fixture lives in `tests/_fixtures/authority_cwd.py`, and `HX016` is allowed on upstream's legacy setter calls (3186).
- Full-suite fixes after the merge touch worker env/client, failed-result service, state DB file/schema/sidecar keys (3187).
- The private-scratch note in delegated children follows the brief on fresh runs only; continue runs do not get it (3190).
- Fork-local plugin catalog pin for `dike` is kept through the upstream sync (3209).
- Remaining patches only adjust file-line caps (`prompt_builder`, `conversation_loop`, `kanban`, `kanban_tools`), regenerate the serve handler ledger, and adapt tests to upstream's claimed admissions, `resumes_parent`, and the unviewed-delegation success rule (success resumes; a requested stop stays a note).

## How it works
- 3186, 3188, 3189, 3193, 3197 move or shorten code to satisfy line-count ratchets.
- 3187, 3192, 3194, 3195 align fork tests and fixtures with upstream's claim/completion contracts.
- 3190 and 3191 gate the note in `tools/delegate_tool_child_run.py` on fresh runs and drop an unused `noqa`.
- 3196 regenerates `tests/hermes_cli/serve_handler_ledger.json`.
- 3209 rewrites `plugin-catalog/dike.yaml` to retain the fork pin.

## Code
- `agent/file_safety.py`, `agent/prompt_builder.py`, `agent/conversation_loop.py`
- `hermes_cli/kanban.py`, `hermes_cli/kanban_notify_identity.py`, `hermes_cli/web_server_cron.py`
- `hermes_cli/kanban_failed_result_service.py`, `hermes_cli/kanban_worker_client.py`, `hermes_cli/kanban_worker_env.py`
- `hermes_state_dbfile.py`, `hermes_state_schema.py`, `hermes_state_sidecar_keys.py`
- `tools/kanban_tools.py`, `tools/delegate_tool_child_run.py`, `tui_gateway/launch_profile_policy.py`
- `tests/_fixtures/authority_cwd.py`, `tests/conftest.py`
- `plugin-catalog/dike.yaml`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- These patches mostly modify existing tests (`tests/gateway/test_model_switch_worker_composed.py`, `tests/tools/test_delegate_private_scratch.py`, `tests/tools/test_delegate_continue.py`, `tests/tools/test_delegate_resumable.py`, `tests/gateway/test_unviewed_delegation_failure_turn.py`, several other `tests/gateway/` files, `tests/cron/test_shutdown_interrupt.py`, `tests/e2e/core/dashboard/test_dashboard_auth.py`, `tests/hermes_state/test_sidecar_keys_registry.py`); no new test names were added.
- The `file_safety` hosted-read change and the plugin catalog pin have no dedicated test in these patches (untested).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The patches assume the upstream `d01ed2bfac` merge bridge is already applied.
