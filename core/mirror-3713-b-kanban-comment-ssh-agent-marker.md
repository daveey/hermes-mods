# LC_HERMES_AGENT marker so agent comments over ssh stay agent-written

Patches: **3704**.

## Behavior
- A Kanban comment written by an agent through `ssh <host> hermes kanban comment ...` is recorded as agent-written, not human, because the marker survives the ssh hop.
- When the marker value looks like a session id, it becomes the comment's `writer_session`; the value `1` names no session. A blank marker means no marker.
- A person's own shell sets no marker, so their ssh comments stay human; a leaked marker only lowers authority.

## How it works
- Stock OpenSSH forwards and accepts `LC_*` variables by default, while `HERMES_*` variables are dropped by hosts that accept only `LANG LC_*`. 3704 adds `LC_HERMES_AGENT` to the agent env markers in `kanban_comment_writer`, with helpers `ssh_agent_marker_value`, `stamp_ssh_agent_marker` and `marker_session`.
- It is exported wherever `HERMES_AGENT` is: the terminal command wrapper (session id, else inherited, else `1`; excluded from the shell snapshot), the local subprocess env, the execute_code child, Kanban worker env (reset to `1`, no stale session), the main/gateway entrypoints and MCP stdio servers. `drop_agent_attribution` removes it so the Desktop GUI never starts with it (untested).

## Code
- `hermes_cli/kanban_comment_writer.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/main.py`
- `hermes_cli/top_level_env.py`
- `gateway/run.py`
- `tools/environments/base_session_env.py`
- `tools/environments/local.py`
- `tools/code_execution_env.py`
- `tools/mcp_tool_config.py`
- `tests/hermes_cli/test_kanban_cli_comment_writer.py`
- `tests/hermes_cli/test_kanban_worker_session_source.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_cli_comment_writer.py`: `test_ssh_session_from_an_agent_is_an_agent`, `test_blank_ssh_marker_is_human`, `test_person_ssh_session_is_human`, `test_ssh_marker_names_the_writer_session_only_when_session_shaped`, `test_ssh_marker_value_is_the_session_id_else_1`, `test_terminal_wrapper_exports_ssh_marker_with_the_session_id`, `test_terminal_snapshot_does_not_persist_the_ssh_marker`, `test_local_subprocess_env_exports_ssh_marker`, `test_execute_code_child_env_exports_ssh_marker`, `test_entrypoints_advertise_the_ssh_marker`, `test_mcp_stdio_server_env_carries_the_ssh_marker`.
- `tests/hermes_cli/test_kanban_worker_session_source.py`: `test_worker_env_carries_the_ssh_marker_without_a_stale_session`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Depends on the remote ssh server accepting `LC_*` (the OpenSSH default); a server that blocks that is not helped.
- The unscrubbed `build_subprocess_env` path is deliberately not stamped, to stay byte-preserving.
