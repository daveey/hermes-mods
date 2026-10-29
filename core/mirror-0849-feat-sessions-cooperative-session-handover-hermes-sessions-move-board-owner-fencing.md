# feat(sessions): cooperative session handover: hermes sessions move + Board owner fencing [daveey]

Patch 0849, source `59009a4979358773526eb8871f60304a6888887e`.

## Behavior

feat(sessions): cooperative session handover: hermes sessions move + Board owner fencing [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `class SyncUnavailable(RuntimeError):`
- `class Hub:`
- `def _import_foreign_resume(args) -> None:`
- `def _resolve_chat_session_args(args, use_tui: bool) -> None:`
- `+"""Cooperative session handover: hermes sessions move and its target-side verbs. [daveey]`
- `+"""Which gateway owns a chat session, on the Board service (session handover). [daveey]`
- `def _cmd_set_journal_mode(args):`
- `def build_sessions_parser(subparsers, *, cmd_sessions: Callable) -> None:`
- `CONFIGURABLE_TOOLSETS = [`
- `class FakeService:`
- `class FakeHub:`
- `+"""Session handover: the Board owner record, fencing, and hermes sessions move end to end. [daveey]`
- `+"""session_move: the agent tool starts hermes sessions move ... --after-turn detached. [daveey]"""`
- `+"""session_move: a chat hands itself over to another gateway after this turn. [daveey]`
- `_HERMES_CORE_TOOLS = [`
- `TOOLSETS = {`
- `def _admit_prompt_turn(`
- `def _durable_owner_refusal(session: dict):`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> A chat moves to another gateway while this one (its owner) is alive and signs the move:
> - hermes_cli/session_owner.py: owner(s)/move against the Board's new POST /v2/boards/<id>/sessions
>   (hermes-hub branch session-owners) through the kanban sync client and gateway key; board = the default
>   board's primary sync binding (sync off / not primary = no record, no fencing); 30 s per-session cache;
>   unreachable Board = last answer, else fail-open with a warning. refusal(sid) -> SESSION_MOVED.
> - Fencing: tui_gateway _admit_prompt_turn checks it before the slot claim (_session_moved_refusal), and
>   hermes --resume / -z --resume exit 1 with the same refusal.
> - hermes sessions move <id> --to <gw> --reason .. [--after-turn]: refuse mid-turn (durable turn lease) or
>   wait up to 10 min; export lineage + source_home; push hidden via sessions.move.transport (default ssh to
>   sessions.move.hosts[gw]); verify counts; Board CAS (epoch from the record, 0 if none); reveal there;
>   archive here (moved_to_gateway:<gw>); re-point open cards created/shepherded by the chat to a
>   platform='hub' sub <gw>/<profile>/<sid> and drop the tui sub. A CAS failure deletes the hidden copy.
> - Target verbs: sessions import-lineage <file|-> [--hidden] (cwd remap, replaces a copy it once handed
>   away), sessions reveal <id>, sessions discard-hidden <id>... (never deletes a visible session).
> - Agent tool session_move(to, reason) (toolset session_move, in the core chat list; check_fn refuses
>   Kanban workers and unconfigured hosts) spawns the --after-turn move detached.
> - Fake Board gains the sessions route, mirroring the Worker.
>
> Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

## Code

- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/main.py`
- `hermes_cli/session_handover.py`
- `hermes_cli/session_owner.py`
- `hermes_cli/sessions_cmd.py`
- `hermes_cli/subcommands/sessions.py`
- `hermes_cli/tools_config.py`
- `tools/session_move_tool.py`
- `toolsets.py`
- `tui_gateway/prompt_turn.py`
- `tui_gateway/session_lifecycle.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/kanban_sync_fake_board.py`
- `tests/hermes_cli/test_session_handover.py`
- `tests/tools/test_session_move_tool.py`

New or changed test definitions present in the source diff:

- `test_owner_client_cas_outcomes_and_30s_cache`
- `test_owner_client_unreachable_board_uses_last_answer_else_fails_open`
- `test_no_record_and_no_fencing_when_sync_is_off`
- `test_ksc_hub_sessions_returns_a_409_refusal_body`
- `test_admit_prompt_turn_refuses_a_moved_session`
- `test_cli_resume_of_a_moved_session_exits_with_session_moved`
- `test_oneshot_resume_of_a_moved_session_exits_before_the_turn`
- `test_sessions_move_end_to_end_copies_reveals_archives_fences_and_repoints`
- `test_a_card_done_after_the_move_notifies_the_copy_on_the_new_gateway`
- `test_move_refused_mid_turn_and_after_turn_waits_for_the_turn_to_end`
- `test_a_cas_failure_removes_the_hidden_copy`
- `test_move_needs_a_configured_host_and_a_reason`
- `test_import_lineage_replaces_a_copy_this_gateway_handed_away`
- `test_transport_argv_templates`
- `test_the_tool_spawns_the_after_turn_move_detached`
- `test_the_tool_refuses_a_target_not_in_sessions_move_hosts`
- `test_the_tool_is_offered_to_chats_with_hosts_only_never_to_kanban_workers`
- `test_the_tool_is_registered_in_the_session_move_toolset`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
