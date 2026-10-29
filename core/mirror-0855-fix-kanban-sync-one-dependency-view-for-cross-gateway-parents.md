# fix(kanban sync): one dependency view for cross-gateway parents [daveey]

Patch 0855, source `4a16a716f79f8d0a2d234100aacf5f8c8a66d809`.

## Behavior

fix(kanban sync): one dependency view for cross-gateway parents [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def recompute_ready(conn: sqlite3.Connection, failure_limit: int | None = None)`
- `def unsatisfied_parents(conn: sqlite3.Connection, task_id: str) -> list[tuple[st`
- `def promote_task(`
- `CREATE TABLE IF NOT EXISTS kanban_sync_outbox (`
- `def _db_key(conn: sqlite3.Connection) -> Optional[str]:`
- `def transient_rejection(res: dict) -> bool:`
- `def _unacked_parents(conn: sqlite3.Connection, rows: list[dict], gateway: str) -`
- `def own_partition(conn: sqlite3.Connection, changes: list[sync.RemoteChange], ga`
- `def sync_board(slug: str, path: Path, hub: Hub, gateway: str) -> RoundResult:`
- `def candidates(replica: sqlite3.Connection, own: str, *, now_ms: Optional[int] =`
- `class FakeService:`
- `+"""One dependency view for the promoter, the claim and the Board (2026-10-28 incident).`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> A primary board's live file dropped every link whose parent another gateway
> owns (own_partition required both ends to be ours), and kanban_db read a
> link to an absent parent as satisfied (JOIN tasks). So the owner's promoter
> kept marking a child ready that the Board refused to lease
> (parents_not_done): 60 promoted/claim_rejected events in 7 min on
> 2026-10-28 (t_01a184ac93d31979 waiting on worker-1's t_01a184d5273673a3).
>
> - own_partition keeps a link whose child is ours.
> - The sync round mirrors each foreign parent's Board status from the
>   replica into the local-only kanban_sync_remote_parents table and wakes
>   the dispatcher when one moves.
> - kanban_db.parent_states is the one view recompute_ready, claim_task,
>   unsatisfied_parents and promote_task read; a parent on neither the board
>   nor the mirror is missing and gates. A todo child with a missing parent
>   gets a parent_missing event and is blocked after 10 min with
>   'parent <id> missing from this board' (parent_found closes the streak).
> - Push sends unacked parent/child task rows ahead of a link, and retries a
>   link the Board refuses 'not_found: unknown task(s)' instead of dropping it.
> - Worker nodes no longer copy a ready replica card with an open or missing
>   parent.
> - The fake Board refuses links to unknown cards, like rows.ts upsertLink.

## Code

- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_worker_node.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/kanban_sync_fake_board.py`
- `tests/hermes_cli/test_kanban_cross_gateway_parents.py`

New or changed test definitions present in the source diff:

- `test_a_child_whose_parent_is_missing_is_not_promoted_then_blocked_after_the_timeout`
- `test_a_forced_ready_child_with_a_missing_parent_is_not_claimed`
- `test_a_parent_that_comes_back_closes_the_missing_streak`
- `test_the_normal_parent_child_flow_is_unchanged`
- `test_a_foreign_parent_gates_its_child_on_the_owner_until_it_is_done`
- `test_a_link_from_our_card_to_a_foreign_child_stays_out_of_the_live_board`
- `test_a_link_never_reaches_the_board_ahead_of_its_parent_card`
- `test_a_link_the_board_refuses_for_an_unknown_card_is_retried_not_dropped`
- `test_a_worker_node_does_not_copy_a_ready_card_whose_parent_is_open`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
