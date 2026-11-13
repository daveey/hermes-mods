# Pickup ranking tolerates a candidate that finished between reads

Patches: **3577**.

## Behavior
- On the worker-node pickup path, a candidate that finishes between the candidate listing and the ranker's fresh replica read no longer raises `KeyError`; it is ranked by the row it was listed with, so cross-gateway ordering is kept instead of falling back to local order.
- If the row lacks priority/created_at columns, it ranks as zero.
- Genuine replica read failures still log a warning and fall back.

## How it works
3577 changes `rank()` in `kanban_dispatch_priority` to use `tasks.get(tid, row)`; priority and created_at are read through `_field()`, which returns 0 when a column is absent. The vanished row is not added to the `tasks` map, since rows there must carry status for the child-graph filter.

## Code
- `hermes_cli/kanban_dispatch_priority.py`
- `tests/hermes_cli/test_kanban_pickup_priority.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_pickup_priority.py`: `test_candidate_finished_between_reads_keeps_its_own_priority`, `test_vanished_candidate_without_rank_columns_ranks_as_zero`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Only the ranker is covered; the full worker-node pickup entry path is untested here.
