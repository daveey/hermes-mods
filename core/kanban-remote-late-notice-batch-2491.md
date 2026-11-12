# Late remote kanban notices are batched with transcript-safe grouped custody

Patches: **2479, 2480, 2481**.

## Behavior

Remote kanban notices whose owner time is older than the fresh window (`FRESH_S`) are no longer delivered one wake each. For a chat, the sweep and the poller group them into one "Late notices — remote updates:" message that lists each item with its owner-time stamp (Pacific time, DST aware), its age, and the original text (first 20, then "+N more"). Fresh notices keep their original text and path. A group wakes the chat once; a group made only of human-blocked members stays passive, and a mixed group takes the first non-human member's wake policy.

Custody is per key: a keyed batch counts as already delivered only if every `kanban_event_keys` entry is already in the session lineage. A partial overlap raises `KanbanNoticeOverlapError`; the caller re-renders only the missing members and retries, never acknowledging an existing row as custody for absent keys. A refusal or render failure releases every member's undelivered claim; a persisted chat is never un-claimed by a later wake failure. 2481 makes batch claims use `_notice_state` so a late request-withdrawal keeps its `request_withdrawn@<event>` ledger state instead of acking a different row.

## How it works

`tui_gateway/kanban_remote_late.py`: `owner_time`, `group`, `collect_groups`, `missing_texts`, `release_undelivered`, and `SweepBatches` (a stand-in waker target that defers delivery while the ordinary link guards and claims run, then `flush` delivers one group per `(profile, tip)` and isolates failures per chat). `tui_gateway/kanban_remote_notices.py` adds `owner_at` to notice items, excludes late items from `MAX_PER_POLL`, groups in `collect`, and makes `ack`, `release` and `_settle_started` fan out over `children`. `hermes_state_messages.record_kanban_notice` performs the all-keys check inside the writer transaction; 2480 moves it to `hermes_state_notice_keys.existing_notice`. `tui_gateway/session_notifications.py` carries the item on each text (`_kb_remote`). See [kanban-remote-notice-sweep.md](kanban-remote-notice-sweep.md) and [kanban-notice-custody-bound-2409a.md](kanban-notice-custody-bound-2409a.md).

## Code

`tui_gateway/kanban_remote_late.py`, `tui_gateway/kanban_remote_notices.py`, `tui_gateway/session_notifications.py`, `hermes_state_messages.py`, `hermes_state_notice_keys.py`, `hermes_state_errors.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/tui_gateway/test_kanban_remote_late.py` (sweep and poller grouping, crash recovery, refusal release, Pacific DST stamps, destination guards, partial overlap retry, group cap, late withdrawal state) and `tests/tui_gateway/test_kanban_remote_overlap.py` (writer-level partial and full overlap). Both the sweep and the poller doors are exercised by the parametrized tests; no other delivery path is claimed.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- The late-notice timestamps are hard-coded to the `America/Los_Angeles` zone.
- Full key coverage may span several existing rows; partial coverage always retries.
- `release_undelivered` only deletes undelivered ledger rows, so it is safe after a partial failure.

## Plugin

Core change; no plugin required.
