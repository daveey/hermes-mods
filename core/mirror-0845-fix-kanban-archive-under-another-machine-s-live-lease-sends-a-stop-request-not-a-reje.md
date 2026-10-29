# fix(kanban): archive under another machine's live lease sends a stop request, not a rejected write [daveey]

Patch 0845, source `4206276bd9f7cffcdcca3262e0e8592ce449d854`.

## Behavior

fix(kanban): archive under another machine's live lease sends a stop request, not a rejected write [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _cmd_archive(args: argparse.Namespace) -> int:`
- `class DispatchResult:`
- `def _run_reclaim_phase(`
- `def _upsert_row(conn: sqlite3.Connection, ch: RemoteChange, own: str, result: Ap`
- `+"""Archive a card another machine is running: a stop request through the Board, acted on by the lease holder. [daveey]`
- `def _unacked_parents(conn: sqlite3.Connection, rows: list[dict], gateway: str) -`
- `def push(conn: sqlite3.Connection, hub: Hub, board: str, gateway: str, *, primar`
- `_OVERRIDE_OPS = (`
- `def update_task(task_id: str, payload: UpdateTaskBody, board: Optional[str] = Qu`
- `def _bulk_apply_one(conn, tid: str, payload: BulkTaskBody, board: Optional[str],`
- `+"""Archiving a card another machine runs under its live Board lease (kanban_remote_stop). [daveey]`
- `def _kanban_handler(tool_name: str, *, classify_rejections: bool = False) -> Cal`
- `def _handle_archive(args: dict, **kw) -> str:`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> kanban_archive (tool, CLI, dashboard) on a card another gateway runs under its live Board
> lease used to write 'archived' locally and report success; the Board rejected the status
> write (live_claim), the card read back running and the remote worker kept going.
>
> Now the requester appends an archive_requested event (events are not lease-fenced) and
> returns {status: archive_pending, holder}; no local 'archived' event. The holder pulls the
> event, which wakes its dispatcher; that tick archives the card there (host-local worker
> terminated, holder's status write accepted). A board whose capture would not carry the
> request refuses with code remote_live_lease naming the holder.
>
> Other lifecycle tools (complete/block/unblock/schedule/continue/request_review/
> request_changes) on such a card are refused up front (rejection live_claim, holder), and
> any task write the Board still rejects live_claim leaves a sync_rejected event on the card.

## Code

- `hermes_cli/kanban.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/kanban_remote_stop.py`
- `hermes_cli/kanban_sync_client.py`
- `plugins/kanban/dashboard/plugin_api.py`
- `tools/kanban_tools.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_remote_archive.py`

New or changed test definitions present in the source diff:

- `test_tool_archive_under_a_remote_lease_is_pending_and_writes_no_archived_event`
- `test_cli_archive_under_a_remote_lease_is_pending`
- `test_the_holder_stops_its_worker_at_the_next_tick_and_the_card_reads_archived`
- `test_a_stop_request_for_an_earlier_run_does_not_stop_the_current_one`
- `test_archive_of_a_host_local_running_card_behaves_as_before`
- `test_archive_is_refused_with_the_holder_named_when_the_board_cannot_carry_a_stop_request`
- `test_other_lifecycle_tools_on_a_remote_lease_are_refused_with_the_holder`
- `test_a_status_write_the_board_rejects_live_claim_is_recorded_on_the_card`
- `test_dashboard_archive_under_a_remote_lease_is_pending`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
