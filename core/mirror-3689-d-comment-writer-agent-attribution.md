# Kanban comment writer: automation and handle attribution

Patches: **3678**.

## Behavior
- Comments written by commands started under non-interactive automation runners (SSM agent/document worker, cron, atd) record `agent` instead of `human`; an interactive SSM session worker below such a runner stays human.
- Comments from a process whose cgroup v2 leaf is a system or user `.service` record `agent` (`runner:systemd:<unit>`); login/session scopes and other scopes keep the normal walk.
- An `--author` of the form `<gateway>/t_<16 hex>` or `hub-chat-<hex>` turns a clean walk into `agent`, logged as an override.
- Each rule only lowers authority; an agent walk keeps its own evidence and the typed slash-command path stays human.

## How it works
3678 extends `kanban_comment_writer.py` with a runner rule (ancestor names, including the 15-char comm prefix), a systemd cgroup rule, and an author-handle rule. Process names and the cgroup are consulted only with the real reader, so substituted fakes are unaffected. `kanban.py` gets a small wiring change.

## Code
- `hermes_cli/kanban.py`
- `hermes_cli/kanban_comment_writer.py`
- `tests/hermes_cli/test_kanban_cli_comment_writer.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_cli_comment_writer.py`: `test_ssm_send_command_tree_is_an_agent_at_ssm_document_worker`, `test_tree_rooted_at_an_automation_runner_is_an_agent`, `test_interactive_ssm_session_stays_human`, `test_human_login_shell_under_real_reader_stays_human`, `test_cli_writer_records_runner_evidence`, `test_cli_comment_over_ssm_send_command_records_agent`, `test_agent_handle_author_records_agent_over_a_clean_walk`, `test_other_authors_with_a_clean_walk_stay_human`, `test_handle_author_does_not_relabel_an_agent_walk`, `test_cli_comment_with_handle_author_from_clean_shell_records_agent`, `test_unreadable_process_name_only_skips_the_runner_rule`, `test_none_author_is_not_a_handle`, `test_monkeypatched_reader_reads_no_real_names`, `test_systemd_service_cgroup_records_agent_over_a_clean_walk`, `test_other_cgroups_keep_the_human_walk`, `test_app_scope_keeps_an_agent_walk_unchanged`, `test_service_cgroup_does_not_relabel_an_agent_walk`, `test_cgroup_v2_path_parses_the_unified_line`, `test_monkeypatched_reader_reads_no_real_cgroup`, `test_real_reader_consults_the_real_cgroup`, `test_cli_comment_from_a_systemd_service_records_agent`, `test_typed_slash_command_inside_a_service_stays_human`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- cgroup v1-only hosts and macOS launchd are not covered.
- Forwarding provenance markers through relays is out of scope.
