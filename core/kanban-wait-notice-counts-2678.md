# Wait-notice slot counts per board and unused-grant ready clocks

Patches: **2601, 2635**.

## Behavior

- The "N/M worker slots busy" figure in ready-wait notices and card reads (`hermes kanban show`, chat-wait and parent-unclaimed notices) is the host's running workers across all active boards, counted relative to the board of the connection being read. Previously the other-board sum excluded the process's current board, so a notice on one board could report 0/7 while another board ran 7, or count a board twice.
- Other boards' files are opened read-only (no migration, no write lock). If any board cannot be enumerated or read, the count is **unknown**: notices say `running count unknown (cap N)`, never a zero or a partial sum. In-memory connections have an unknown board and count. Boards pointing at one file count once.
- Notices and queue positions carry `board`, `observed_at` and an admission reason (or an explicit `unknown`); text ends with `[board <b>, observed HH:MM:SSZ, admission reason <r | unknown>]`. A core-update hold reports reason `core_update_hold`. Old payloads without those fields render unchanged.
- A Board grant that was claimed and returned unused (a bare reservation: released run, no worker, no lock, metadata only `unused_grant`) no longer resets a card's ready clock. A card whose grants keep bouncing keeps its real waiting age in notices and diagnostics. Real execution, a lost worker or a ready transition still reset the clock; a grant carrying any execution trace counts as execution.
- From a worker process (fenced, read-only connection), cards on a Board-primary board that are leased to another gateway's pool workers are not counted as this host's running slots (2635). Before, a worker's `kanban show` could say `14/7 running` while the gateway held 6.

## How it works

- 2601: new `hermes_cli/kanban_host_counts.py` (`conn_db_path`, `board_of`, `running_on_host`, `_running_in_file`); `kanban_queue_position` uses `running_on_host` and adds `board` and `observed_at` to the place; `kanban_parent_unclaimed` adds `_observation` text and the `board`/`observed_at`/`admission` payload fields; `kanban_chat_wait` appends the observation. `kanban_unused_grant` gains pure helpers (`unused_grant_run`, `unused_grant_run_ids`) usable on dataclass rows and event lists; `kanban_diagnostics` drops unused-grant runs and their events before computing the ready floor, and `kanban_chat_wait`'s SQL prefilter ignores their `claimed` events and run ends.
- 2635: `_running_in` reads `kanban_sync_state` from the file being counted (`_own_gateway`): mode `primary` with a `gateway_id` excludes cards whose `lease_holder` is another gateway with no `claim_lock`, the same condition as `held_elsewhere_sql`. No state table, no row, or a non-primary mode counts every running card; a primary row without a gateway id, or unreadable state, makes the count unknown. `board_mode()` and the dispatcher's write paths are unchanged.

## Code

`hermes_cli/kanban_host_counts.py` (new), `hermes_cli/kanban_queue_position.py`, `hermes_cli/kanban_parent_unclaimed.py`, `hermes_cli/kanban_chat_wait.py`, `hermes_cli/kanban_diagnostics.py`, `hermes_cli/kanban_unused_grant.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_wait_notice_board_counts.py`, `tests/hermes_cli/test_kanban_host_counts_held_elsewhere.py`, `tests/gateway/test_kanban_cross_board_slots.py`, `tests/plugins/test_kanban_foreign_card_api.py`, `tests/tools/test_kanban_show_queue.py`. Entry path: the queue-position, chat-wait and parent-unclaimed notice builders, the `kanban_show` tool's queue read, the diagnostics ready-floor function, and `running_on_host` against real temporary board files (read-only worker connection, other-board file, unreadable state, primary without gateway id, leased cards, non-primary modes, shared file, in-memory connection, no writable connection opened). The unused-grant tests cover repeated grants preserving age, grants with a worker lock or any execution trace still resetting, and a lost worker after grants resetting. A running gateway, a live Board and the `hermes kanban show` CLI process itself are **untested**.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- The count is a point-in-time read (`observed_at`), not an admission guarantee; the dispatcher's own verdict decides.
- A board whose sync state is unreadable makes the whole host count unknown rather than falling back to an over-count.
- 2635 removed a test that pinned the old seam (monkeypatching `held_elsewhere_sql`); the new file uses real primary-board rows instead.

## Plugin

Core change; no plugin required.
