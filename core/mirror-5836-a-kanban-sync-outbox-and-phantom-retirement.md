# Kanban sync: empty-outbox skip, repair windows, phantom run retirement (mirror 5469–5471, 5475, 5488)

## Behavior
- The sync board watcher skips opening observers for a proven-empty outbox. Fresh positive/deadline work is retained and the existing `data_version` observation is used. In 5469, main-file device/inode/mtime/size movement reopens the observer (a same-inode restore that keeps SQLite's header counter cannot reuse an empty proof); ordinary WAL commits still use `data_version`. 5471 then narrows the generation effect of that reopen (next bullet).
- For a same-device/inode pager refresh (e.g. a checkpoint) the repair-input generation is preserved (query answers are discarded and the pager reopened) so ordinary checkpoints respect repair windows; replacement, reset and failed-read recovery get a new generation. This supersedes 5470's `test_checkpoint_reopens_watcher_generation` expectation, which 5471 replaces with `test_checkpoint_preserves_watcher_generation`.
- Residual sync-round steps (board discovery, gone-board sweep, selection, deferred repairs, backlog, after-round, report, wait) are wrapped in `sync_cpu.step`, which uses the `sync.step` scope only when loop-accounting scopes are enabled; nested scopes book only residual CPU.
- Rejection retirement now covers fenced phantom `task_runs` as well as `task_events`: the existing predicate is generalized from `task_events` to both tables, with the single tombstone and the rejection audit matched by the op's own table and row uid. Per the docstring, no phantom event/run is replayed and its tombstone is not removed. The other conditions of the pre-existing rule are not re-derived here.

## How it works
`kanban_sync_board_watch.py`, `kanban_sync_outbox_age.py`, `kanban_sync_client.py`, `kanban_sync_cpu.py`, `kanban_sync_rejected_event.py`, `kanban_sync_rejections.py`.

## Code
See How it works (all under `hermes_cli/`).

## Tests
Source: `test_kanban_sync_empty_outbox.py`, `test_kanban_sync_repair_window.py`, `test_kanban_sync_step_accounting.py`, `test_kanban_sync_targeted_rounds.py`, `test_loop_accounting_sync_round.py`, `test_kanban_live_claim_child_rows.py`, `test_kanban_rejected_phantom_event.py` (by test names: a normal round retires a fenced event or run, parametrized by table, keeping audit and tombstone; an evidence read revalidates retirement provenance, parametrized by race and `target_first`; empty-proof, repair-window, step-name, targeted-round and residual-CPU tests listed above); `test_kanban_live_claim_child_rows.py` and `test_kanban_sync_repair_window.py` are touched without new named tests apart from a `without_checkpoint` helper. Not executed in this mirror pass.

## Applying
Apply in order. 5488 only reworks tests of 5475.

## Gotchas
The patches contain no CPU measurement; the empty-outbox skip is a design change whose effect is not demonstrated here.

## plugin?
No.
