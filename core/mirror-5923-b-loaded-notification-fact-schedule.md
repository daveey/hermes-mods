# Loaded-session notification scheduling: board isolation, deadlines, bounded retries, remote isolation

Mirror snapshot 5923; ordinals 5884, 5885 (plus the notification half of 5879).

Entries:

- **5879** (notification half): Preserve notification board isolation and replay accepted view demands; its demand/replica half is in [fact demand and replica transport](mirror-5923-b-fact-demand-replica-transport.md)
- **5884**: Honor notification deadlines and bound incomplete retries
- **5885**: Isolate remote notification failures from local delivery

Builds on the `display.loaded_notification_f` consumer introduced in [shared facts core](mirror-5923-b-shared-facts-core.md).

## Behavior

- **Board isolation (5879)**: local board discovery for loaded sessions reads each board in isolation. An unreadable board (locked, corrupt, missing) is recorded with a reason and contributes nothing (partial rows are discarded), while every healthy board is still discovered and delivered. The failure becomes an explicit incomplete/retry obligation repaid by the next unchanged 30 s reconciliation, never by an accelerated census, and clears on recovery.
- **Deadlines and bounded retries (5884)**: an early report wake from the unloaded-chat sweeper no longer postpones due legacy work a full interval; never-bound work whose reported deadline has passed is treated as due. For loaded sessions, an incomplete reconciliation retries only the keys positively linked to the chat (or its lineage) and profile, so a full capture covering other chats and profiles cannot become a one-second census; unknown recovery waits for the 30 s clock.
- **Remote failure isolation (5885)**: a failing remote hint or remote source capture no longer blocks local delivery. Remote failures are remembered per scope as incomplete until the capture recovers, local delivery continues with no source scan, hint failures retry on the next native hint or the unchanged clock, and "known keys" are only updated from successful captures.

## How it works

- `tui_gateway/notification_loaded_local.py`: `capture(actions)` returns `(boards_by_action, failed)`, one board inventory per profile pass plus bounded `IN` reads for loaded keys.
- `tui_gateway/notification_loaded_f.py`: `local_incomplete`, `remote_incomplete` and `hint_incomplete` state; `_local_obligation(scope, group, failed)` records or clears failed boards and marks each action's session; `_remember(...)` updates known keys only from successful captures; keyed invalidation filters rows by linked chats via `_linked_chats` and the session lineage.
- `tui_gateway/kanban_unloaded.py` and `kanban_unloaded_schedule.py`: maintenance deadline `min(maintenance, schedule.next_legacy)` while legacy work is unbound; `report_due` is read before `due()` resets `retry_at`.

## Code

Shipped non-test paths:

- `tui_gateway/notification_loaded_f.py`
- `tui_gateway/notification_loaded_local.py`
- `tui_gateway/kanban_unloaded.py`
- `tui_gateway/kanban_unloaded_schedule.py`

## Tests

Source-side tests shipped by these entries; none were executed by this documentation pass, so no pass result is claimed here:

- `tests/tui_gateway/test_loaded_local_board_isolation.py` (a bad board is isolated and retried on the next reconcile)
- `tests/tui_gateway/test_kanban_unloaded_fact_schedule.py` (an early report deadline does not postpone legacy work)
- `tests/tui_gateway/test_loaded_notification_f.py` (incomplete reconciliation retries only positive chat keys, plus updated existing cases)
- `tests/tui_gateway/test_loaded_remote_failure_recovery.py`, `tests/tui_gateway/test_loaded_remote_local_isolation.py` (healthy local delivery survives a remote failure; both files define a test of the same name)

## Applying

These entries are part of the ordered snapshot 5923. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying) (`git am -3 --empty=keep` over the full ordered list, plus any named repair overlay). They are not independent patches.

## Gotchas

- Recovery from incomplete discovery is deliberately slow (next 30 s reconciliation); do not "fix" it with a faster retry, which would reintroduce a census.
- With `display.loaded_notification_f` off none of this applies; the legacy per-chat pollers run.
- Failed-board state lives in memory and in the session marker only; it is not persisted.

## Plugin

Core change; no plugin or hook is involved.
