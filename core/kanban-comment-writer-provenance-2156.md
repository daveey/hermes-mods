# Kanban comment provenance: agent versus human writers

Patches **2136, 2139, 2149**.

## Behavior

A comment's `writer_kind` decides whether a worker hears it "from the operator" and whether the ask-gate treats it as the owner's words. Three entry points now record it honestly:

- **Dashboard comment endpoint (2136).** `POST /api/plugins/kanban/tasks/<id>/comments` accepts `writer_kind='agent'` (and an optional `writer_session`, trimmed and clipped to 200 chars) from the Desktop session-mentions courier that serves the agent `cross_task` tool. A client can only lower provenance: with no kind the comment stays `human` (the GUI default); an explicit `human`, any unknown kind, or a session without a kind is HTTP 400 and nothing is stored.
- **Worker spawn context (2139).** In the "Comment thread" of a fresh or respawned worker's context, comments whose `commented` event says `writer_kind='agent'` get an `(agent chat)` tag; human and legacy comments keep the plain stamp, and the existing "comment from worker" framing is unchanged.
- **CLI (2149).** `hermes kanban comment` records `agent` when the process or any ancestor was *started* with an agent-harness marker, and `human` only after a complete ancestry walk finds none. No flag or variable can assert human. `/kanban ...` typed in the CLI composer or a person's gateway platform message passes `human_typed=True` to `run_slash`; API/webhook sources, bot authors and internal events do not.

## How it works

- `plugins/kanban/dashboard/plugin_api_comment_actor.py` holds `CommentBody` and `comment_writer(payload)`; `plugin_api.py` calls it and passes `writer_kind` / `writer_session` to `add_comment`.
- `_ctx_comments` in `kanban_db_context.py` now takes `(lines, conn, task_id, now)`, loads comments itself and calls `agent_comment_ids` for the tag.
- `hermes_cli/kanban_comment_writer.py`: `AGENT_ENV_MARKERS` (worker, delegated child, execute_code RPC socket, session id, `HERMES_AGENT`, `AI_AGENT`, `CLAUDECODE`, supervised child, Desktop backend), `agent_ancestor` walking up to 64 ancestors through a `ProcReader` (psutil; start-time environ, not the mutable `os.environ`, because every Hermes entrypoint `setdefault`s the marker itself), and `cli_comment_writer` returning `(kind, session, evidence)`. An inconclusive walk (psutil missing, vanished ancestor, unreadable same-user process, depth cap) records `agent`; only root-owned unreadable processes (sshd, launchd, setuid login) are skipped so a person's ssh or Terminal shell stays human.
- `run_slash` sets `args._slash_typed` after parsing so no argv spelling can claim it. `gateway/slash_commands.py` excludes `api_server`, `webhook`, `msgraph_webhook`, bots and internal events.
- Environment plumbing: `top_level_env.drop_agent_attribution` strips the markers from the Desktop GUI launch env (so its terminal pane is a person's shell); `tools/mcp_tool_config._build_safe_env` passes `AI_AGENT` / `HERMES_AGENT` to stdio MCP servers.

## Code

- `plugins/kanban/dashboard/plugin_api.py`, `plugin_api_comment_actor.py` (new).
- `hermes_cli/kanban_db_context.py`, `hermes_cli/kanban_db.py`.
- `hermes_cli/kanban.py`, `hermes_cli/kanban_comment_writer.py` (new), `hermes_cli/cli_commands_mixin.py`, `gateway/slash_commands.py`, `hermes_cli/main_desktop.py`, `hermes_cli/top_level_env.py`, `tools/mcp_tool_config.py`.

## Tests

**Source regression coverage; not executed in this mirror documentation pass.** Names come from the source diff; no pass/fail result is claimed.

- 2136, `tests/tools/test_kanban_dashboard_comment_actor.py`: `test_courier_agent_comment_records_agent_and_session`, `test_courier_agent_comment_without_session_is_still_agent`, `test_gui_comment_without_actor_stays_human`, `test_forged_or_malformed_actor_is_refused_and_stores_nothing`, `test_non_string_actor_is_rejected_by_the_schema`, `test_agent_session_is_trimmed_and_clipped`, `test_courier_agent_note_reaches_worker_as_agent`, `test_gui_note_reaches_worker_from_the_operator`.
- 2139, `tests/tools/test_kanban_comment_writer_kind.py`: `test_spawn_context_labels_agent_comments_only`, `test_spawn_context_agent_label_survives_backtick_author`.
- 2149, `tests/hermes_cli/test_kanban_cli_comment_writer.py`: `test_each_marker_marks_an_agent`, `test_human_shell_has_no_agent_ancestor`, `test_agent_terminal_is_found_through_the_parent_shell`, `test_gateway_service_descendant_is_an_agent`, `test_an_incomplete_walk_is_not_human`, `test_depth_cap_is_not_human`, `test_inconclusive_records_agent`, `test_same_user_unreadable_process_is_inconclusive`, `test_human_shell_under_a_real_nondumpable_parent_is_human`, `test_real_process_start_env_is_read_and_later_setdefault_does_not_count`, `test_cli_comment_from_agent_terminal_records_agent`, `test_cli_comment_from_human_shell_records_human`, `test_typed_slash_command_is_human_inside_an_agent_process`, `test_programmatic_run_slash_is_not_human`, `test_gateway_platform_slash_comment_is_human`, `test_gateway_untyped_slash_comment_is_not_human_by_fiat`, `test_mcp_stdio_server_env_carries_the_harness_marker`, `test_desktop_gui_launch_env_drops_the_harness_marker`, `test_desktop_relaunch_env_drops_agent_markers`, `test_cli_composer_slash_comment_is_human`, `test_no_cli_input_asserts_human`, `test_agent_cli_note_reaches_worker_as_another_agent`, `test_human_cli_note_reaches_worker_from_the_operator`, `test_real_cli_subprocess_in_terminal_tool_shell_records_agent`.

## Applying

Apply ordinals 2136, 2139, 2149 as part of the snapshot-aware full replay described in the [README](README.md) (full recipe in its Applying section), in published order, not as independent patches. 2136 builds on the earlier in-process `kanban_comment` writer_kind work, which must already be in place. No claim is made here about live rollout; this page documents source behaviour only.

## Gotchas

- Fail-safe direction: a leaked marker labels a person's note `agent`, which only withholds operator authority.
- Out of reach by design: a same-user process that destroys its marked ancestry (`exec env -i`, double-fork) or hands its command to a process outside it (`tmux send-keys`, `at`, a remote ssh session). That is evasion, not provenance, which would need real authentication.
- The dashboard endpoint still records `human` for any token holder that omits `writer_kind`; the source calls this server-side gap a follow-up.
- On Windows, unreadable ancestors make every walk inconclusive, so CLI comments read as agent.

## Could this be a plugin?

No. The comment event schema, worker context builder, CLI and gateway slash ingress are core. Only the dashboard endpoint is plugin-shaped code, and it lives in the Kanban plugin tree that ships with core.
