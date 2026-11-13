# Remote notice index: bounded memory

Patches: **3670**.

## Behavior
- The remote notice index keeps a decoded card only when it routes to a chat, shrinking retained memory (about 169 MB to 6 MB on a large replica per the commit message).
- Cold reads of task payloads are paged in bounded chunks inside one read snapshot rather than packed into one huge JSON string, lowering peak memory for the index and the sweep.

## How it works
3670 changes the `remote_index_projection` snapshot to retain only routed cards, and `kanban_remote_notices._task_rows` to read identities in one query (same planner order) and payloads in rowid pages of about 4 MB of text, within the caller's snapshot or its own.

## Code
- `tui_gateway/kanban_remote_notices.py`
- `tui_gateway/remote_index_projection.py`
- `tests/tui_gateway/test_remote_index_projection_memory.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tui_gateway/test_remote_index_projection_memory.py`: `test_cold_index_retains_only_routed_cards_and_bounded_peak`, `test_warm_index_keeps_routed_cards_and_routes_a_newly_linked_one`, `test_task_rows_pages_payloads_in_one_snapshot_and_keeps_the_planner_order`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
