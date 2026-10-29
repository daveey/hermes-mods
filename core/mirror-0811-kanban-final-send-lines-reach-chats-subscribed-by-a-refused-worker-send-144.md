# kanban: FINAL SEND lines reach chats subscribed by a refused worker send (#144)

Patch 0811, source `582e43bdd014ffdc9187b3cf4f6303e84892ccc0`.

## Behavior

kanban: FINAL SEND lines reach chats subscribed by a refused worker send (#144). This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _fmt_completed(ev, n) -> tuple:`
- `def complete_task(`
- `def _cleaned_artifact_paths(metadata: Any) -> list[str]:`
- `+"""Completion event data shared by Kanban notification consumers."""`
- `def _worker_log_exit_code(task_id: str, board: Optional[str] = None) -> Optional`
- `+"""FINAL SEND for @session:<ref>: <text> lines in a Kanban completion summary.`
- `def observe_owner_task(conn: sqlite3.Connection, task: dict, *, now: float) -> d`
- `class SessionMessagesMixin:`
- `def test_default_spawn_does_not_auto_load_any_skill(kanban_home, monkeypatch):`
- `def test_completion_entry_records_real_goal_success(board, monkeypatch, entry):`
- `+"""The checkpoint back-off JSON accepts files written with or without a BOM."""`
- `+"""A chat a worker was refused into is subscribed to the worker's card and gets its message on completion.`
- `+"""A remote owner publishes completion evidence; the destination persists one passive notice."""`
- `from __future__ import annotations`
- `def release(claim: dict, *, ledger: Optional[Path] = None) -> bool:`
- `def _loop(stop: threading.Event) -> None:`
- `def _kb_first_line(value: Any, limit: int) -> str:`
- `def _format_kanban_event_text(sub: dict, task, ev, board_slug: str) -> Optional[`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> * feat(kanban): FINAL SEND lines in a completion summary reach the chat a refused worker send subscribed [daveey]
>
> * fix(kanban): carry remote completion notices through owner replicas [daveey]
>
> * fix(checkpoints): accept BOM in slow-folder JSON (inherited lint blocker) [daveey]
>
> * fix(kanban): dedupe notices across compression and opt in new completions [daveey]
>
> * fix(kanban): goal receipts compare the exact judged evidence, not the public preview
>
> * fix(kanban): reap drops a non-Popen so a missing poll() cannot fail the tick
>
> Spawn tests leave FakeProc doubles in _live_worker_procs. The dispatcher
> reaps those handles at the start of each tick, and proc.poll() then
> tracebacks into test_gateway_dispatcher_disables_corrupt_board_without_traceback.
> Drop entries that are not pollable. Also set check=False on the serve
> reexec helper so ruff PLW1510 on main does not fail this PR's CI.
>
> * kanban_db_notify: annotate os.kill liveness probe for footgun check
>
> * kanban_origin_ledger: match judged evidence against FINAL SEND-stripped event preview

## Code

- `gateway/kanban_watchers_notifier.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_completion_payload.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_final_send.py`
- `hermes_cli/kanban_origin_ledger.py`
- `hermes_state_messages.py`
- `tui_gateway/kanban_remote_notices.py`
- `tui_gateway/kanban_unloaded.py`
- `tui_gateway/session_notifications.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_core_functionality.py`
- `tests/hermes_cli/test_kanban_goal_receipt.py`
- `tests/tools/test_checkpoint_slow_bom.py`
- `tests/tui_gateway/test_kanban_final_send.py`
- `tests/tui_gateway/test_kanban_remote_notices.py`

New or changed test definitions present in the source diff:

- `test_reap_drops_non_popen_without_traceback`
- `test_goal_release_survives_final_send_in_evidence`
- `test_slow_folder_backoff_survives_bom`
- `test_subscribed_chat_gets_the_standard_done_notice`
- `test_final_send_line_addressed_to_the_chat_replaces_the_summary`
- `test_final_send_for_another_chat_is_not_shown_here`
- `test_two_final_sends_each_reach_only_their_own_chat`
- `test_hub_subscription_row_matches_by_session_id`
- `test_final_send_first_line_is_not_broadcast_to_other_subscribers`
- `test_result_fallback_does_not_broadcast_addressed_text`
- `test_final_send_only_replaces_completed_events`
- `test_parser_accepts_decorated_and_bare_refs_and_ignores_junk`
- `test_remote_completion_reaches_only_addressed_chat_and_is_idempotent`
- `test_remote_generic_notice_and_busy_retry`
- `test_legacy_done_cards_are_not_replayed_on_rollout`
- `test_absent_replica_is_read_only_and_not_created`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
